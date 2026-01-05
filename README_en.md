# KindlePub

[中文版](./README.md) | English Version

High-performance Image/CBZ to EPUB converter, specifically optimized for **Send to Kindle**.

## Key Features
- **CBZ & ZIP Support**: Directly convert comic archive files without manual extraction.
- **Composition Protection**: No cropping by default. Perfectly preserves the original non-centered layouts and artistic white spaces (ideal for artbooks like *Love Live!*).
- **High Performance**: Multi-threaded processing. Automatically reserves one CPU core to ensure system smoothness during conversion.
- **Smart Scaling**: Automatically limits image height to 2048px using the **LANCZOS** algorithm to balance clarity and device performance.
- **Metadata Extraction**: Automatically parses `ComicInfo.xml`. If missing, it provides an interactive prompt for title and author before extraction.
- **Fixed Layout**: Generates standard EPUB 3 fixed-layout files, perfect for Kindle's official "Send to Kindle" service.

## Installation
Ensure you have Python 3 and the Pillow library installed:
```bash
pip install Pillow
```
or
```bash
pip install -r requirements.txt
```

## Usage
```bash
python3 epub.py [Source_Path/CBZ/ZIP] [Output_Name] [Options]
```

### Examples
1. **Convert CBZ (Primary Goal)**:
   ```bash
   python3 epub.py my_manga.cbz
   ```
2. **Convert Folder with Custom Metadata**:
   ```bash
   python3 epub.py ./my_images_dir --title "My Collection" --author "Artist Name"
   ```
3. **Enable Auto-Cropping** (Only for scanned documents with messy edges):
   ```bash
   python3 epub.py manga.zip --crop
   ```

## Options
| Option | Description |
| :--- | :--- |
| `--title` | Manually specify the book title (overrides auto-detection). |
| `--author` | Manually specify the author (overrides auto-detection). |
| `--crop` | Enable automatic white border cropping (Disabled by default). |
| `--threads` | Specify the number of processing threads (Default: CPU cores - 1). |

## License
MIT
