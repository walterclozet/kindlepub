import os
import sys
import argparse
import zipfile
import uuid
import tempfile
import shutil
import datetime
import xml.etree.ElementTree as ET
from xml.sax.saxutils import escape
from PIL import Image
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_default_threads():
    """获取推荐线程数：CPU 核心数 - 1"""
    count = os.cpu_count()
    return count - 1 if count and count > 1 else 1

def parse_xml_content(content):
    """解析 ComicInfo.xml 获取元数据"""
    info = {'title': None, 'author': None}
    try:
        root = ET.fromstring(content)
        info['title'] = root.findtext('Title')
        info['author'] = root.findtext('Writer')
    except: pass
    return info

def get_metadata_preflight(input_path, user_title, user_author):
    """预检逻辑：获取书名作者，支持从 ComicInfo.xml 提取"""
    final_title, final_author = user_title, user_author
    xml_info = {'title': None, 'author': None}
    is_archive = os.path.isfile(input_path) and input_path.lower().endswith(('.cbz', '.zip'))
    
    if os.path.isdir(input_path):
        xml_path = os.path.join(input_path, 'ComicInfo.xml')
        if os.path.exists(xml_path):
            with open(xml_path, 'rb') as f: xml_info = parse_xml_content(f.read())
    elif is_archive:
        try:
            with zipfile.ZipFile(input_path, 'r') as z:
                if 'ComicInfo.xml' in z.namelist():
                    xml_info = parse_xml_content(z.read('ComicInfo.xml'))
        except: pass

    if not final_title: final_title = xml_info['title']
    if not final_author: final_author = xml_info['author']

    if not final_title or not final_author:
        print("\n--- 补充元数据 (Send to Kindle 必需) ---")
        default_name = os.path.splitext(os.path.basename(input_path.rstrip(os.sep)))[0]
        if not final_title:
            val = input(f"请输入书名 (默认: {default_name}): ").strip()
            final_title = val if val else default_name
        if not final_author:
            val = input(f"请输入作者 (默认: Unknown): ").strip()
            final_author = val if val else "Unknown"
    return final_title, final_author

def process_single_image(args):
    """处理单张图片：裁剪 -> 缩放 -> 转 JPEG"""
    img_path, i, do_crop = args
    try:
        img = Image.open(img_path)
        if do_crop:
            if img.mode != 'RGB': img = img.convert('RGB')
            bbox = img.getbbox()
            if bbox: img = img.crop(bbox)

        # 针对 Scribe 的适配，限制高度但保持比例
        MAX_HEIGHT = 2480 
        if img.height > MAX_HEIGHT:
            ratio = MAX_HEIGHT / float(img.height)
            img = img.resize((int(img.width * ratio), MAX_HEIGHT), Image.Resampling.LANCZOS)
        
        width, height = img.size
        img_io = BytesIO()
        img.save(img_io, format='JPEG', quality=85)
        
        return {
            'index': i, 'filename': f"i_{i:04d}.jpg",
            'data': img_io.getvalue(), 'w': width, 'h': height
        }
    except Exception as e: return f"Error: {e}"

def create_epub(input_path, output_file, do_crop, max_workers, user_title, user_author):
    final_title, final_author = get_metadata_preflight(input_path, user_title, user_author)
    safe_title = escape(final_title)
    safe_author = escape(final_author)
    
    if not output_file:
        input_base = os.path.splitext(os.path.basename(input_path.rstrip(os.sep)))[0]
        output_file = f"{input_base}.epub"

    temp_dir = None
    try:
        if os.path.isfile(input_path) and input_path.lower().endswith(('.cbz', '.zip')):
            temp_dir = tempfile.mkdtemp()
            print(f"[*] 正在提取归档: {os.path.basename(input_path)}")
            with zipfile.ZipFile(input_path, 'r') as z: z.extractall(temp_dir)
            process_dir = temp_dir
        else:
            process_dir = input_path

        exts = ('.jpg', '.jpeg', '.png', '.webp')
        files = sorted([os.path.join(process_dir, f) for f in os.listdir(process_dir) 
                       if f.lower().endswith(exts) and f.lower() != 'comicinfo.xml'])
        total = len(files)
        if total == 0:
            print("[-] 错误: 未发现图片文件"); return

        print(f"[*] 转换中: 《{final_title}》 (线程数: {max_workers})")
        
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_single_image, (p, i, do_crop)) for i, p in enumerate(files)]
            for future in as_completed(futures):
                res = future.result()
                if isinstance(res, dict): 
                    results.append(res)
                    print(f"\r    进度: [{len(results)}/{total}] {int(len(results)/total*100)}% ", end='', flush=True)

        results.sort(key=lambda x: x['index'])
        book_uuid = f"urn:uuid:{uuid.uuid4()}"
        # 使用 UTC 时间确保合规性
        mod_time = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

        with zipfile.ZipFile(output_file, 'w', compression=zipfile.ZIP_DEFLATED) as epub:
            # 严格标准：第一个文件必须是 mimetype 且不压缩
            epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            epub.writestr('META-INF/container.xml', '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

            manifest, spine, nav_list = [], [], []
            for res in results:
                idx = res['index']
                img_href, xhtml_href = f"Images/{res['filename']}", f"Text/p_{idx:04d}.xhtml"
                epub.writestr(f"OEBPS/{img_href}", res['data'])
                
                # 为每一页注入 Viewport 以适配 Scribe
                html = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <meta charset="UTF-8" />
    <title>{idx}</title>
    <meta name="viewport" content="width={res['w']}, height={res['h']}"/>
    <style>body{{margin:0;padding:0;background-color:#FFFFFF;}} img{{width:100%;height:auto;display:block;}}</style>
</head>
<body><img src="../{img_href}" alt="p{idx}"/></body>
</html>'''
                epub.writestr(f"OEBPS/{xhtml_href}", html)
                
                cover_prop = ' properties="cover-image"' if idx == 0 else ''
                manifest.append(f'<item id="img{idx}" href="{img_href}" media-type="image/jpeg"{cover_prop}/>')
                manifest.append(f'<item id="p{idx}" href="{xhtml_href}" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="p{idx}"/>')
                nav_list.append(f'<li><a href="{xhtml_href}">Page {idx+1}</a></li>')

            # EPUB 3 导航
            nav_content = f'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"><head><meta charset="UTF-8" /><title>Navigation</title></head><body><nav epub:type="toc"><h1>Table of Contents</h1><ol>{"".join(nav_list)}</ol></nav></body></html>'
            epub.writestr('OEBPS/nav.xhtml', nav_content)
            manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

            # NCX 导航 (用于老版本兼容，dtb:uid 必须与 OPF 一致)
            ncx_nav = [f'<navPoint id="n{i}" playOrder="{i+1}"><navLabel><text>Page {i+1}</text></navLabel><content src="Text/p_{i:04d}.xhtml"/></navPoint>' for i in range(total)]
            ncx_content = f'<?xml version="1.0" encoding="UTF-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head><meta name="dtb:uid" content="{book_uuid}"/><meta name="dtb:depth" content="1"/></head><docTitle><text>{safe_title}</text></docTitle><navMap>{"".join(ncx_nav)}</navMap></ncx>'
            epub.writestr('OEBPS/toc.ncx', ncx_content)
            manifest.append('<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>')

            # 核心 OPF 元数据
            opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="pub-id" version="3.0" prefix="rendition: http://www.idpf.org/vocab/rendition/#" xml:lang="zh">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="pub-id">{book_uuid}</dc:identifier>
        <dc:title>{safe_title}</dc:title>
        <dc:creator>{safe_author}</dc:creator>
        <dc:language>zh</dc:language>
        <meta property="dcterms:modified">{mod_time}</meta>
        <meta property="rendition:layout">pre-paginated</meta>
        <meta property="rendition:orientation">auto</meta>
        <meta property="rendition:spread">none</meta>
        <meta name="cover" content="img0"/>
        <meta name="book-type" content="comic"/>
    </metadata>
    <manifest>{"".join(manifest)}</manifest>
    <spine toc="ncx">{"".join(spine)}</spine>
</package>'''
            epub.writestr('OEBPS/content.opf', opf)
        print(f"\n[+] 最终合规版生成成功: {output_file}")
    finally:
        if temp_dir: shutil.rmtree(temp_dir)

if __name__ == "__main__":
    if len(sys.argv) == 1:
        print("📖 KindlePub (Fixed Final)\n用法: python3 epub.py [输入路径] [输出路径]"); sys.exit(0)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("input_path")
    parser.add_argument("output_file", nargs='?')
    parser.add_argument("--title", type=str)
    parser.add_argument("--author", type=str)
    parser.add_argument("--crop", action="store_true")
    parser.add_argument("--threads", type=int, default=get_default_threads())
    args = parser.parse_args()
    create_epub(args.input_path, args.output_file, args.crop, args.threads, args.title, args.author)
