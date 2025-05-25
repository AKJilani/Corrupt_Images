from flask import Flask, render_template, request, jsonify
import os
from PIL import Image, ImageFile
import threading
import time
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
import multiprocessing
import struct
import random
import mmap

# Enable loading of truncated images for better detection
ImageFile.LOAD_TRUNCATED_IMAGES = True

app = Flask(__name__)

# Global variable to store processing status
processing_status = {
    'is_processing': False,
    'current_folder': '',
    'total_folders': 0,
    'processed_folders': 0,
    'total_images': 0,
    'processed_images': 0,
    'corrupt_images': [],
    'message': '',
    'start_time': None,
    'images_per_second': 0
}

def quick_file_check(file_path):
    """Ultra-fast preliminary file checks"""
    try:
        # Check file size
        size = os.path.getsize(file_path)
        if size == 0:
            return True  # Empty file is corrupt
        
        # Check file extension vs actual format
        with open(file_path, 'rb') as f:
            header = f.read(12)
            
        # Quick header validation for common formats
        if len(header) < 4:
            return True
            
        # JPEG header check
        if header[:2] == b'\xff\xd8':
            if size < 100:  # Too small for valid JPEG
                return True
            # Check if JPEG ends properly
            with open(file_path, 'rb') as f:
                f.seek(-2, 2)
                end_bytes = f.read(2)
                if end_bytes != b'\xff\xd9':
                    return True  # JPEG doesn't end properly
                    
        # PNG header check
        elif header[:8] == b'\x89PNG\r\n\x1a\n':
            if size < 50:  # Too small for valid PNG
                return True
                
        # GIF header check
        elif header[:6] in [b'GIF87a', b'GIF89a']:
            if size < 20:  # Too small for valid GIF
                return True
                
        return False  # Passed quick checks
        
    except (OSError, IOError, PermissionError):
        return False  # Don't mark as corrupt if we can't access file

def deep_corruption_check(image_path):
    """Extremely accurate corruption detection with minimal resource usage"""
    try:
        # Step 1: Quick file validation
        if quick_file_check(image_path):
            return True
            
        # Step 2: PIL opening and basic validation
        with Image.open(image_path) as img:
            # Validate basic properties
            if not hasattr(img, 'size') or not img.size or img.size[0] <= 0 or img.size[1] <= 0:
                return True
                
            # Store image info
            width, height = img.size
            mode = img.mode
            format_type = img.format
            
            # Validate format
            if format_type is None:
                return True
            
            # Step 3: Try to load image data (lazy loading test)
            try:
                img.load()
            except (OSError, IOError) as e:
                if any(keyword in str(e).lower() for keyword in 
                       ['truncated', 'corrupt', 'broken', 'invalid', 'damaged']):
                    return True
                return False  # Other errors don't necessarily mean corruption
            
            # Step 4: Strategic pixel sampling (ultra-fast)
            try:
                # Convert to RGB for consistent pixel access
                if mode in ('P', 'L'):
                    test_img = img.convert('RGB')
                elif mode == 'RGBA':
                    test_img = img.convert('RGBA')
                else:
                    test_img = img
                
                # Test critical pixels only (corners + center + random sample)
                critical_points = [
                    (0, 0),  # Top-left
                    (width-1, 0),  # Top-right
                    (0, height-1),  # Bottom-left
                    (width-1, height-1),  # Bottom-right
                    (width//2, height//2),  # Center
                ]
                
                # Add 3 random points for better coverage
                for _ in range(3):
                    x = random.randint(0, width-1)
                    y = random.randint(0, height-1)
                    critical_points.append((x, y))
                
                # Test all critical points
                for x, y in critical_points:
                    try:
                        pixel = test_img.getpixel((x, y))
                        # Validate pixel data
                        if pixel is None:
                            return True
                    except (IndexError, ValueError, TypeError):
                        return True
                        
            except Exception as e:
                if any(keyword in str(e).lower() for keyword in 
                       ['truncated', 'corrupt', 'broken', 'invalid', 'damaged']):
                    return True
                return False
        
        # Step 5: Final verification (re-open for verify)
        try:
            with Image.open(image_path) as img:
                img.verify()
        except Exception as e:
            error_msg = str(e).lower()
            # Only mark as corrupt for specific corruption errors
            corruption_keywords = [
                'truncated', 'corrupt', 'broken', 'invalid', 'damaged',
                'premature end', 'incomplete', 'bad', 'error'
            ]
            if any(keyword in error_msg for keyword in corruption_keywords):
                return True
            return False  # Other errors might be format-related, not corruption
            
        return False  # All tests passed - image is good
        
    except PermissionError:
        return False  # Don't mark as corrupt if we can't access
    except Exception as e:
        # Final safety check - only mark as corrupt for known corruption errors
        error_msg = str(e).lower()
        corruption_keywords = [
            'truncated', 'corrupt', 'broken', 'invalid', 'damaged',
            'premature end', 'incomplete', 'bad data'
        ]
        return any(keyword in error_msg for keyword in corruption_keywords)

def process_single_image_batch(image_batch):
    """Process a batch of images in a single process"""
    corrupt_images = []
    
    for image_path, folder_name, filename in image_batch:
        if deep_corruption_check(image_path):
            corrupt_images.append({'folder': folder_name, 'image': filename})
    
    return corrupt_images

def create_image_batches(image_tasks, batch_size=50):
    """Create batches of images for processing"""
    for i in range(0, len(image_tasks), batch_size):
        yield image_tasks[i:i + batch_size]

def count_images_in_folders(main_folder_path, folder_names):
    """Count total images for progress tracking"""
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}
    total = 0
    
    for folder_name in folder_names:
        folder_path = os.path.join(main_folder_path, folder_name.strip())
        if os.path.exists(folder_path):
            try:
                files = os.listdir(folder_path)
                for filename in files:
                    if os.path.isfile(os.path.join(folder_path, filename)):
                        file_ext = os.path.splitext(filename)[1].lower()
                        if file_ext in image_extensions:
                            total += 1
            except:
                continue
    return total

def process_folders_ultra_fast(main_folder_path, folder_names):
    """Ultra-fast processing using optimized multiprocessing"""
    global processing_status
    
    processing_status['is_processing'] = True
    processing_status['corrupt_images'] = []
    processing_status['total_folders'] = len(folder_names)
    processing_status['processed_folders'] = 0
    processing_status['processed_images'] = 0
    processing_status['start_time'] = time.time()
    
    # Count total images first
    processing_status['total_images'] = count_images_in_folders(main_folder_path, folder_names)
    
    # Supported image extensions
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}
    
    # Collect all image tasks
    image_tasks = []
    
    for folder_name in folder_names:
        folder_name = folder_name.strip()
        if not folder_name:
            continue
            
        processing_status['current_folder'] = folder_name
        folder_path = os.path.join(main_folder_path, folder_name)
        
        if not os.path.exists(folder_path):
            processing_status['processed_folders'] += 1
            continue
            
        # Collect all image files in the folder
        try:
            files = os.listdir(folder_path)
            for filename in files:
                file_path = os.path.join(folder_path, filename)
                
                if os.path.isfile(file_path):
                    file_ext = os.path.splitext(filename)[1].lower()
                    if file_ext in image_extensions:
                        image_tasks.append((file_path, folder_name, filename))
        except Exception as e:
            print(f"Error accessing folder {folder_name}: {str(e)}")
        
        processing_status['processed_folders'] += 1
    
    # Process images using optimized multiprocessing
    if image_tasks:
        # Use optimal number of processes (CPU cores * 2 for I/O bound tasks)
        max_processes = min(multiprocessing.cpu_count() * 2, 16)  # Cap at 16 to avoid overhead
        
        # Create batches for better efficiency
        batch_size = max(10, len(image_tasks) // (max_processes * 4))
        image_batches = list(create_image_batches(image_tasks, batch_size))
        
        with ProcessPoolExecutor(max_workers=max_processes) as executor:
            # Submit all batches
            future_to_batch = {executor.submit(process_single_image_batch, batch): batch for batch in image_batches}
            
            processed_batches = 0
            # Process results as they complete
            for future in as_completed(future_to_batch):
                try:
                    batch_results = future.result()
                    processing_status['corrupt_images'].extend(batch_results)
                    
                    # Update progress
                    batch = future_to_batch[future]
                    processing_status['processed_images'] += len(batch)
                    processed_batches += 1
                    
                    # Calculate speed
                    elapsed_time = time.time() - processing_status['start_time']
                    if elapsed_time > 0:
                        processing_status['images_per_second'] = int(processing_status['processed_images'] / elapsed_time)
                    
                except Exception as e:
                    print(f"Error processing batch: {str(e)}")
                    # Still update progress even if batch failed
                    batch = future_to_batch[future]
                    processing_status['processed_images'] += len(batch)
    
    # Save results to file
    save_results()
    processing_status['is_processing'] = False

def get_desktop_path():
    """Get the desktop path for current user"""
    if os.name == 'nt':  # Windows
        return os.path.join(os.path.expanduser('~'), 'Desktop')
    else:  # Mac/Linux
        return os.path.join(os.path.expanduser('~'), 'Desktop')

def get_unique_filename(folder_path, base_name, extension='.txt'):
    """Get a unique filename by appending numbers if file exists"""
    counter = 1
    filename = f"{base_name}{extension}"
    full_path = os.path.join(folder_path, filename)
    
    while os.path.exists(full_path):
        counter += 1
        filename = f"{base_name} {counter}{extension}"
        full_path = os.path.join(folder_path, filename)
    
    return full_path

def save_results():
    """Save corrupt images list to Desktop in 'Corrupt Image' folder"""
    try:
        # Get desktop path
        desktop_path = get_desktop_path()
        
        # Create 'Corrupt Image' folder on desktop if it doesn't exist
        corrupt_folder = os.path.join(desktop_path, 'Corrupt Image')
        if not os.path.exists(corrupt_folder):
            os.makedirs(corrupt_folder)
        
        # Get unique filename
        file_path = get_unique_filename(corrupt_folder, 'Corrupt Image')
        
        # Calculate final stats
        total_time = time.time() - processing_status['start_time'] if processing_status['start_time'] else 0
        final_speed = int(processing_status['total_images'] / total_time) if total_time > 0 else 0
        
        # Save the file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("Folder\tImages\n")  # Header
            for item in processing_status['corrupt_images']:
                f.write(f"{item['folder']}\t{item['image']}\n")
        
        processing_status['result_file'] = file_path
        processing_status['message'] = f'Processed {processing_status["total_images"]} images in {total_time:.1f}s ({final_speed} images/sec). Found {len(processing_status["corrupt_images"])} corrupt images. Results saved to: {file_path}'
        
    except Exception as e:
        processing_status['message'] = f'Error saving file: {str(e)}'

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/start_processing', methods=['POST'])
def start_processing():
    data = request.json
    main_folder_path = data.get('folder_path', '').strip()
    folder_names_input = data.get('folder_names', '').strip()
    
    if not main_folder_path or not folder_names_input:
        return jsonify({'error': 'Please provide both folder path and folder names'}), 400
    
    if not os.path.exists(main_folder_path):
        return jsonify({'error': 'Main folder path does not exist'}), 400
    
    if processing_status['is_processing']:
        return jsonify({'error': 'Processing is already in progress'}), 400
    
    # Split folder names by newline and filter empty lines
    folder_names = [name.strip() for name in folder_names_input.split('\n') if name.strip()]
    
    if not folder_names:
        return jsonify({'error': 'Please provide at least one folder name'}), 400
    
    # Start processing in a separate thread
    thread = threading.Thread(target=process_folders_ultra_fast, args=(main_folder_path, folder_names))
    thread.daemon = True
    thread.start()
    
    return jsonify({'message': 'Ultra-fast processing started successfully'})

@app.route('/get_status')
def get_status():
    return jsonify(processing_status)

if __name__ == '__main__':
    # Optimize for high-performance processing
    multiprocessing.set_start_method('spawn', force=True)
    app.run(debug=False, port=5000, threaded=True)