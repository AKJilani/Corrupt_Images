from flask import Flask, render_template, request, jsonify
import os
from PIL import Image
import threading
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing

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
    'message': ''
}

def check_image_corruption_fast(image_path):
    """Accurately check if an image is truly corrupt at pixel level"""
    try:
        # First, check if file exists and has reasonable size
        if not os.path.exists(image_path) or os.path.getsize(image_path) == 0:
            return True
            
        # Try to open and validate the image
        with Image.open(image_path) as img:
            # Basic validation
            if img.format is None:
                return True
                
            width, height = img.size
            if width <= 0 or height <= 0:
                return True
            
            # Try to load the image completely - this is crucial
            img.load()
            
            # Convert to a standard format to ensure pixel access works
            if img.mode in ('RGBA', 'LA'):
                test_img = img.convert('RGBA')
            else:
                test_img = img.convert('RGB')
            
            # Test pixel accessibility at key points
            try:
                # Test corners
                test_img.getpixel((0, 0))
                test_img.getpixel((width-1, 0))  
                test_img.getpixel((0, height-1))
                test_img.getpixel((width-1, height-1))
                
                # Test center
                test_img.getpixel((width//2, height//2))
                
                # Test a few random points
                import random
                for _ in range(5):
                    x = random.randint(0, width-1)
                    y = random.randint(0, height-1)
                    test_img.getpixel((x, y))
                
            except (IndexError, ValueError, OSError):
                return True
        
        # Re-open to verify image integrity (verify() closes the image)
        with Image.open(image_path) as img:
            # This will raise an exception if the image is corrupted
            img.verify()
            
        # If we reach here, the image is likely good
        return False
        
    except (IOError, OSError) as e:
        # These are typical corruption errors
        error_msg = str(e).lower()
        if any(keyword in error_msg for keyword in ['truncated', 'corrupt', 'invalid', 'broken', 'damaged']):
            return True
        # If it's just a format we don't support, don't mark as corrupt
        return False
        
    except (ValueError, TypeError) as e:
        # These might indicate corruption
        error_msg = str(e).lower()
        if any(keyword in error_msg for keyword in ['corrupt', 'invalid', 'truncated', 'broken']):
            return True
        return False
        
    except Exception as e:
        # For any other unexpected error, be conservative
        error_msg = str(e).lower()
        if any(keyword in error_msg for keyword in ['corrupt', 'truncated', 'invalid', 'broken', 'damaged']):
            return True
        # If we can't determine, assume it's not corrupt
        return False

def process_single_image(args):
    """Process a single image - designed for multiprocessing"""
    image_path, folder_name, filename = args
    
    if check_image_corruption_fast(image_path):
        return {'folder': folder_name, 'image': filename}
    return None

def count_images_in_folders(main_folder_path, folder_names):
    """Count total images for progress tracking"""
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.tiff', '.webp', '.ico'}
    total = 0
    
    for folder_name in folder_names:
        folder_path = os.path.join(main_folder_path, folder_name.strip())
        if os.path.exists(folder_path):
            try:
                for filename in os.listdir(folder_path):
                    if os.path.isfile(os.path.join(folder_path, filename)):
                        file_ext = os.path.splitext(filename)[1].lower()
                        if file_ext in image_extensions:
                            total += 1
            except:
                continue
    return total

def process_folders_fast(main_folder_path, folder_names):
    """Process folders with maximum speed using multiprocessing"""
    global processing_status
    
    processing_status['is_processing'] = True
    processing_status['corrupt_images'] = []
    processing_status['total_folders'] = len(folder_names)
    processing_status['processed_folders'] = 0
    processing_status['processed_images'] = 0
    
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
            for filename in os.listdir(folder_path):
                file_path = os.path.join(folder_path, filename)
                
                if os.path.isfile(file_path):
                    file_ext = os.path.splitext(filename)[1].lower()
                    if file_ext in image_extensions:
                        image_tasks.append((file_path, folder_name, filename))
        except Exception as e:
            print(f"Error accessing folder {folder_name}: {str(e)}")
        
        processing_status['processed_folders'] += 1
    
    # Process images using multiprocessing for maximum speed
    if image_tasks:
        # Use optimal number of workers (CPU cores)
        max_workers = min(multiprocessing.cpu_count(), len(image_tasks))
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # Submit all tasks
            future_to_task = {executor.submit(process_single_image, task): task for task in image_tasks}
            
            # Process results as they complete
            for future in as_completed(future_to_task):
                try:
                    result = future.result()
                    if result:  # Image is corrupt
                        processing_status['corrupt_images'].append(result)
                except Exception as e:
                    print(f"Error processing image: {str(e)}")
                
                # Update progress
                processing_status['processed_images'] += 1
    
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
        
        # Save the file
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write("Folder\tImages\n")  # Header
            for item in processing_status['corrupt_images']:
                f.write(f"{item['folder']}\t{item['image']}\n")
        
        processing_status['result_file'] = file_path
        processing_status['message'] = f'Found {len(processing_status["corrupt_images"])} corrupt images. Results saved to: {file_path}'
        
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
    thread = threading.Thread(target=process_folders_fast, args=(main_folder_path, folder_names))
    thread.daemon = True
    thread.start()
    
    return jsonify({'message': 'Processing started successfully'})

@app.route('/get_status')
def get_status():
    return jsonify(processing_status)

if __name__ == '__main__':
    app.run(debug=True, port=5000, threaded=True)