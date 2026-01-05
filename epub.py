import os
import sys
import argparse
import zipfile
import uuid
import tempfile
import shutil
import xml.etree.ElementTree as ET
from PIL import Image
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor, as_completed

def get_default_threads():
    count = os.cpu_count()
    return count - 1 if count and count > 1 else 1

def parse_xml_content(content):
    """解析 ComicInfo.xml 的字节流"""
    info = {'title': None, 'author': None}
    try:
        root = ET.fromstring(content)
        info['title'] = root.findtext('Title')
        info['author'] = root.findtext('Writer')
    except:
        pass
    return info

def get_metadata_preflight(input_path, user_title, user_author):
    """【解压前预检】确定书名和作者"""
    final_title, final_author = user_title, user_author
    xml_info = {'title': None, 'author': None}

    # 1. 尝试从文件夹或 ZIP 目录中偷窥 ComicInfo.xml
    if os.path.isdir(input_path):
        xml_path = os.path.join(input_path, 'ComicInfo.xml')
        if os.path.exists(xml_path):
            with open(xml_path, 'rb') as f:
                xml_info = parse_xml_content(f.read())
    elif os.path.isfile(input_path) and input_path.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(input_path, 'r') as z:
                if 'ComicInfo.xml' in z.namelist():
                    xml_info = parse_xml_content(z.read('ComicInfo.xml'))
        except:
            pass

    # 2. 优先级合并
    if not final_title: final_title = xml_info['title']
    if not final_author: final_author = xml_info['author']

    # 3. 交互询问逻辑
    if not final_title or not final_author:
        print("\n--- 补充书籍元数据 ---")
        default_name = os.path.splitext(os.path.basename(input_path.rstrip(os.sep)))[0]
        if not final_title:
            val = input(f"请输入书名 (回车使用默认: {default_name}): ").strip()
            final_title = val if val else default_name
        if not final_author:
            val = input(f"请输入作者 (回车使用默认: Unknown): ").strip()
            final_author = val if val else "Unknown"
        print("--------------------\n")

    return final_title, final_author

def process_single_image(args):
    """处理单张图片逻辑：裁边(选配) -> 缩放(强制) -> 转换"""
    img_path, i, do_crop = args
    try:
        img = Image.open(img_path)
        # 只有显式加了 --crop 才执行
        if do_crop:
            if img.mode != 'RGB': img = img.convert('RGB')
            bbox = img.getbbox()
            if bbox: img = img.crop(bbox)

        # 高度限制在 2048px，使用高质量 LANCZOS
        MAX_HEIGHT = 2048
        if img.height > MAX_HEIGHT:
            ratio = MAX_HEIGHT / float(img.height)
            img = img.resize((int(img.width * ratio), MAX_HEIGHT), Image.Resampling.LANCZOS)

        img_io = BytesIO()
        ext = os.path.splitext(img_path)[1].lower()
        save_format = 'JPEG' if ext in ('.jpg', '.jpeg') else 'PNG'
        img.save(img_io, format=save_format, quality=85)
        
        return {
            'index': i, 
            'filename': f"image_{i:04d}.{save_format.lower()}",
            'data': img_io.getvalue(), 
            'media_type': f"image/{'jpeg' if save_format=='JPEG' else 'png'}"
        }
    except Exception as e:
        return f"Error: {e}"

def create_epub(input_path, output_file, do_crop, max_workers, user_title, user_author):
    # 第一步：解压前预检
    final_title, final_author = get_metadata_preflight(input_path, user_title, user_author)
    
    if not output_file:
        output_file = f"{final_title}.epub"

    temp_dir = None
    try:
        # 第二步：准备处理目录
        if os.path.isfile(input_path) and input_path.lower().endswith('.zip'):
            temp_dir = tempfile.mkdtemp()
            print(f"[*] 正在解压资源...")
            with zipfile.ZipFile(input_path, 'r') as z:
                z.extractall(temp_dir)
            process_dir = temp_dir
        else:
            process_dir = input_path

        # 第三步：图片处理
        exts = ('.jpg', '.jpeg', '.png', '.webp')
        files = sorted([os.path.join(process_dir, f) for f in os.listdir(process_dir) 
                       if f.lower().endswith(exts) and f.lower() != 'comicinfo.xml'])
        
        total = len(files)
        if total == 0:
            print("[-] 错误: 未发现有效图片文件"); return

        print(f"[*] 书名: {final_title} | 作者: {final_author}")
        print(f"[*] 正在转换 {total} 张图片 (线程: {max_workers}):")
        
        results = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(process_single_image, (p, i, do_crop)) for i, p in enumerate(files)]
            for future in as_completed(futures):
                res = future.result()
                if isinstance(res, dict): 
                    results.append(res)
                    print(f"\r    进度: [{len(results)}/{total}] {int(len(results)/total*100)}% ", end='', flush=True)
                else:
                    print(f"\n[!] {res}")

        # 第四步：封装 EPUB
        print(f"\n[*] 正在生成 EPUB 文件...")
        results.sort(key=lambda x: x['index'])
        with zipfile.ZipFile(output_file, 'w', compression=zipfile.ZIP_DEFLATED) as epub:
            epub.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
            epub.writestr('META-INF/container.xml', '<?xml version="1.0" encoding="UTF-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>')

            manifest, spine = [], []
            for res in results:
                i = res['index']
                img_href, xhtml_href = f"Images/{res['filename']}", f"Text/p_{i:04d}.xhtml"
                epub.writestr(f"OEBPS/{img_href}", res['data'])
                # CSS 保持 LoveLive 原版布局，不拉伸
                html = f'<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE html><html xmlns="http://www.w3.org/1999/xhtml"><head><style>body{{margin:0;padding:0;background:#fff;}}img{{max-width:100%;max-height:100%;display:block;margin:auto;}}</style></head><body><img src="../{img_href}"/></body></html>'
                epub.writestr(f"OEBPS/{xhtml_href}", html)
                manifest.append(f'<item id="i{i}" href="{img_href}" media-type="{res["media_type"]}"/>')
                manifest.append(f'<item id="p{i}" href="{xhtml_href}" media-type="application/xhtml+xml"/>')
                spine.append(f'<itemref idref="p{i}"/>')

            opf = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="pub-id" version="3.0">
    <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
        <dc:identifier id="pub-id">urn:uuid:{uuid.uuid4()}</dc:identifier>
        <dc:title>{final_title}</dc:title>
        <dc:creator>{final_author}</dc:creator>
        <dc:language>zh</dc:language>
        <meta property="rendition:layout">pre-paginated</meta>
    </metadata>
    <manifest>{"".join(manifest)}</manifest>
    <spine>{"".join(spine)}</spine>
</package>'''
            epub.writestr('OEBPS/content.opf', opf)
        print(f"[+] 完成！文件保存在: {output_file}")

    finally:
        if temp_dir: shutil.rmtree(temp_dir)

def show_guide():
    print(f"""
🚀 EPUB 画册快速生成工具
---------------------------------------
用法: python3 epub.py [文件夹/ZIP] [输出路径] [参数]

参数说明:
  --title "书名"    --author "作者"
  --crop           裁切白边 (默认关闭，保护原版排版)
  --threads N      指定线程 (默认自动分配: {get_default_threads()})

提示:
  1. 支持 ComicInfo.xml 自动识别。
  2. 若无元数据且未传参，会在解压前询问你。
---------------------------------------
""")

if __name__ == "__main__":
    if len(sys.argv) == 1: show_guide(); sys.exit(0)
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("input_path")
    parser.add_argument("output_file", nargs='?')
    parser.add_argument("--title", type=str)
    parser.add_argument("--author", type=str)
    parser.add_argument("--crop", action="store_true")
    parser.add_argument("--threads", type=int, default=get_default_threads())
    args = parser.parse_args()
    create_epub(args.input_path, args.output_file, args.crop, args.threads, args.title, args.author)
