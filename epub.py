import os
import sys
import argparse
import zipfile
import uuid
import tempfile
import shutil
import datetime  # 新增：用于生成强制时间戳
import xml.etree.ElementTree as ET
from PIL import Image
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_default_threads():
    count = os.cpu_count()
    return count - 1 if count and count > 1 else 1

def parse_xml_content(content):
    info = {'title': None, 'author': None}
    try:
        root = ET.fromstring(content)
        info['title'] = root.findtext('Title')
        info['author'] = root.findtext('Writer')
    except: pass
    return info

def get_metadata_preflight(input_path, user_title, user_author):
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
        print("--------------------\n")
    return final_title, final_author

def process_single_image(args):
    img_path, i, do_crop = args
    try:
        img = Image.open(img_path)
        if do_crop:
            if img.mode != 'RGB': img = img.convert('RGB')
            bbox = img.getbbox()
            if bbox: img = img.crop(bbox)

        MAX_HEIGHT = 2048
        if img.height > MAX_HEIGHT:
            ratio = MAX_HEIGHT / float(img.height)
            img = img.resize((int(img.width * ratio), MAX_HEIGHT), Image.Resampling.LANCZOS)
        
        # 记录尺寸用于 viewport
        width, height = img.size
        img_io = BytesIO()
        ext = os.path.splitext(img_path)[1].lower()
        save_format = 'JPEG' if ext in ('.jpg', '.jpeg', '.webp') else 'PNG'
        img.save(img_io, format=save_format, quality=85)
        
        return {
            'index': i, 'filename': f"i_{i:04d}.{save_format.lower()}",
            'data': img_io.getvalue(), 
            'media_type': f"image/{'jpeg' if save_format=='JPEG' else 'png'}",
            'w': width, 'h': height
        }
    except Exception as e: return f"Error: {e}"

def create_epub(input_path, output_file, do_crop, max_workers, user_title, user_author):
    final_title, final_author = get_metadata_preflight(input_path, user_title, user_author)
    
    # 默认输出文件名保持与输入源一致，避免文件名编码问题
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
        if total == 0: print("[-] 错误: 未发现图片"); return

        print(f"[*] 针对 Kindle 优化转换: 《{final_title}》 | 线程: {max_workers}")
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
        mod_time = datetime.datetime.now(datetime.UTC).strftime('%Y-%m-%dT%H:%M:%SZ')

        with zipfile.ZipFile(output_file, 'w', compression=zipfile.ZIP_DEFLATED) as epub:
            epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            epub.writestr('META-INF/container.xml', '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

            manifest, spine, nav_list = [], [], []
            for res in results:
                idx = res['index']
                img_href, xhtml_href = f"Images/{res['filename']}", f"Text/p_{idx:04d}.xhtml"
                epub.writestr(f"OEBPS/{img_href}", res['data'])
                
                # 修复核心：增加 viewport 元标签
                html = f'''<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
    <title>{idx}</title>
    <meta name="viewport" content="width={res['w']}, height={res['h']}"/>
    <style>body{{margin:0;padding:0;background:#fff;}}img{{width:100%;height:100%;display:block;}}</style>
</head>
<body><img src="../{img_href}" alt="p{idx}"/></body>
</html>'''
                epub.writestr(f"OEBPS/{xhtml_href}", html)
                
                # 第一张图标记为封面
                props = ' properties="cover-image"' if idx == 0 else ''
                manifest.append(f'<item id="img{idx}" href="{img_href}" media-type="{res["media_type"]}"{props}/>')
                manifest.append(f'<item id="p{idx}" href="{xhtml_href}" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="p{idx}"/>')
                nav_list.append(f'<li><a href="{xhtml_href}">Page {idx+1}</a></li>')

            # 必需：EPUB 3 导航文件
            nav_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>Navigation</title></head>
<body>
    <nav epub:type="toc"><ol>{"".join(nav_list)}</ol></nav>
    <nav epub:type="landmarks"><ol><li><a epub:type="cover" href="Text/p_0000.xhtml">Cover</a></li></ol></nav>
</body>
</html>'''
            epub.writestr('OEBPS/nav.xhtml', nav_content)
            manifest.append('<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>')

            opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="pub-id" version="3.0" prefix="rendition: http://www.idpf.org/vocab/rendition/#">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="pub-id">{book_uuid}</dc:identifier>
        <dc:title>{final_title}</dc:title>
        <dc:creator>{final_author}</dc:creator>
        <dc:language>zh</dc:language>
        <meta property="dcterms:modified">{mod_time}</meta>
        <meta property="rendition:layout">pre-paginated</meta>
        <meta property="rendition:orientation">auto</meta>
        <meta property="rendition:spread">none</meta>
        <meta name="cover" content="img0"/>
    </metadata>
    <manifest>{"".join(manifest)}</manifest>
    <spine>{"".join(spine)}</spine>
</package>'''
            epub.writestr('OEBPS/content.opf', opf)
        print(f"\n[+] 转换完成: {output_file}")
    finally:
        if temp_dir: shutil.rmtree(temp_dir)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("input_path")
    parser.add_argument("output_file", nargs='?')
    parser.add_argument("--title", type=str); parser.add_argument("--author", type=str); parser.add_argument("--crop", action="store_true"); parser.add_argument("--threads", type=int, default=get_default_threads())
    args = parser.parse_args()
    create_epub(args.input_path, args.output_file, args.crop, args.threads, args.title, args.author)
