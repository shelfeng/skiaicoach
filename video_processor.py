
import os
import time
import json
import logging
import base64
import subprocess
from abc import ABC, abstractmethod
from dotenv import load_dotenv

# Optional imports
try:
    import google.generativeai as genai
    HAS_GEMINI = True
except ImportError:
    HAS_GEMINI = False

try:
    import openai
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False

# Hybrid Approach: Try imports, if fail, fallback to external ffmpeg
try:
    import imageio.v3 as iio
    import numpy as np
    HAS_IMAGEIO = True
except ImportError:
    HAS_IMAGEIO = False

load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Functions ---

def extract_frames_for_display(video_path, output_folder, num_frames=10):
    """
    Extracts frames using:
    1. imageio (if available - works on Py3.11/3.12)
    2. local ffmpeg.exe (fallback - works on Py3.14 Win)
    """
    if not os.path.exists(output_folder):
        os.makedirs(output_folder)
        
    extracted_paths = []

    # STRATEGY 1: ImageIO (Preferred for Azure/Linux)
    if HAS_IMAGEIO:
        try:
            logger.info("Attempting frame extraction with ImageIO...")
            
            # METHOD A: Try to get total frames safely
            total_frames = 0
            try:
                props = iio.improps(video_path)
                total_frames = props.shape[0]
            except Exception:
                pass

            # Validate total_frames (handle negative or huge numbers seen in logs)
            if total_frames > 0 and total_frames < 1000000:
                # Optimized Path: We know the count
                indices = np.linspace(0, total_frames - 1, num_frames, dtype=int)
                for i, idx in enumerate(indices):
                    try:
                        frame = iio.imread(video_path, index=idx)
                        filename = f"frame_{i}_{int(time.time())}.jpg"
                        filepath = os.path.join(output_folder, filename)
                        iio.imwrite(filepath, frame)
                        extracted_paths.append(filename)
                    except Exception as read_err:
                        logger.warning(f"Failed to read frame {idx}: {read_err}")
                        continue
            else:
                # Robust Path: Iterate without knowing count
                # We will just take frames with a stride estimation or 1fps
                logger.info(f"Total frames unknown or invalid ({total_frames}). Using iterator.")
                
                # Iterate and pick every Kth frame? 
                # Since we don't know length, we can't do perfect spacing.
                # Strategy: Collect frames at roughly 1fps (assuming 30fps)
                frames_captured = 0
                stride = 30 # Assume 30fps, take 1 frame/sec
                
                for i, frame in enumerate(iio.imiter(video_path)):
                    if i % stride == 0:
                        filename = f"frame_{frames_captured}_{int(time.time())}.jpg"
                        filepath = os.path.join(output_folder, filename)
                        iio.imwrite(filepath, frame)
                        extracted_paths.append(filename)
                        frames_captured += 1
                        
                        if frames_captured >= num_frames:
                            break
            
            if extracted_paths:
                return extracted_paths

        except Exception as e:
            logger.warning(f"ImageIO extraction failed: {e}. Falling back to FFMPEG.")

    # STRATEGY 2: Subprocess FFMPEG (Fallback for Py3.14 / Local)
    # Path to local ffmpeg
    ffmpeg_exe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if not os.path.exists(ffmpeg_exe):
        # Try global path (Linux/Mac system ffmpeg)
        ffmpeg_exe = "ffmpeg"

    try:
        logger.info(f"Attempting frame extraction with FFMPEG ({ffmpeg_exe})...")
        output_pattern = os.path.join(output_folder, "frame_%03d.jpg")
        
        # Command: ffmpeg -i input.mp4 -vf fps=1 out_%03d.jpg
        cmd = [
            ffmpeg_exe, 
            "-i", video_path, 
            "-vf", "fps=1", 
            "-y", # Overwrite
            output_pattern
        ]
        
        # On Linux, verify permissions if it's the local binary
        if os.name != 'nt' and ffmpeg_exe.endswith("ffmpeg"):
             # It's system ffmpeg, assumptions are safe.
             pass
        
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        
        # Now list the files
        files = sorted([f for f in os.listdir(output_folder) if f.startswith("frame_") and f.endswith(".jpg")])
        
        # If we have too many, sample them down
        if len(files) > num_frames:
            step = len(files) / num_frames
            indices = [int(i * step) for i in range(num_frames)]
            extracted_paths = [files[i] for i in indices]
        else:
            extracted_paths = files

    except Exception as e:
        logger.error(f"Method 2 (FFMPEG) failed: {e}")
        return []

    return extracted_paths

def encode_image_base64(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# --- LLM Abstraction ---

class SkiCoachAI(ABC):
    @abstractmethod
    def analyze(self, video_path, frames_dir, display_frames) -> dict:
        pass

class GeminiCoach(SkiCoachAI):
    def __init__(self, model_name):
        self.model_name = model_name
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
             logger.warning("GEMINI_API_KEY not set.")
        elif HAS_GEMINI:
             genai.configure(api_key=self.api_key)

    def analyze(self, video_path, frames_dir, display_frames):
        if not HAS_GEMINI:
            return {"error": "Google GenAI library not installed."}
            
        model = genai.GenerativeModel(self.model_name)
        
        logger.info(f"Uploading file {video_path} to Gemini...")
        video_file = genai.upload_file(video_path)
        
        # Wait for processing
        while video_file.state.name == "PROCESSING":
            logger.info("Waiting for video processing...")
            time.sleep(2)
            video_file = genai.get_file(video_file.name)
            
        if video_file.state.name == "FAILED":
            raise ValueError("Video processing failed.")
            
        logger.info(f"Video ready: {video_file.uri}")

        prompt = """
        你是一位专业的滑雪教练。请分析提供的视频。
        重点关注：
        1. 转弯的形状和对称性 (Turn shape and symmetry)
        2. 立刃角度和压力 (Edge angle and pressure)
        3. 上身姿势和平衡 (Upper body posture and balance)
        
        请用 **简体中文 (Simplified Chinese)** 提供分析结果。
        
        请提供以下 JSON 格式的输出：
        {
            "overall_technique_score": (1-10 之间的数字),
            "key_observations": ["观察点 1", "观察点 2"],
            "technical_advice": "详细的改进建议...",
            "frame_by_frame_analysis": [
                {"frame_index": 0, "comment": "转弯入弯阶段..."},
                ...
            ]
        }
        """

        response = model.generate_content([prompt, video_file], generation_config={"response_mime_type": "application/json"})
        
        try:
            result = json.loads(response.text)
            return result
        except Exception as e:
            logger.error(f"Failed to parse JSON: {e}")
            return {"error": "Failed to parse analysis result", "raw": response.text}

class OpenAICoach(SkiCoachAI):
    def __init__(self, model_name):
        self.model_name = model_name
        
        # Check for Azure OpenAI config
        self.azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.azure_api_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.azure_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        
        # Check for Standard OpenAI config
        self.openai_api_key = os.getenv("OPENAI_API_KEY")

        if not self.azure_api_key and not self.openai_api_key:
             logger.warning("Neither AZURE_OPENAI_API_KEY nor OPENAI_API_KEY set.")

    def analyze(self, video_path, frames_dir, display_frames):
        if not HAS_OPENAI:
             return {"error": "OpenAI library not installed."}
        if not display_frames:
             return {"error": "No frames extracted. Check if FFMPEG is installed or downloaded."}

        # Initialize Client (Azure or Standard)
        if self.azure_endpoint and self.azure_api_key:
            logger.info(f"Using Azure OpenAI Service (Endpoint: {self.azure_endpoint})")
            from openai import AzureOpenAI
            client = AzureOpenAI(
                api_key=self.azure_api_key,  
                api_version=self.azure_api_version,
                azure_endpoint=self.azure_endpoint
            )
        elif self.openai_api_key:
            logger.info("Using Standard OpenAI API")
            client = openai.OpenAI(api_key=self.openai_api_key)
        else:
            return {"error": "Missing OpenAI/Azure API Keys."}
        
        # Prepare messages with images
        content = [
            {"type": "text", "text": """
            你是一位专业的滑雪教练。请分析提供的视频帧序列。
            重点关注：
            1. 转弯的形状和对称性 (Turn shape and symmetry)
            2. 立刃角度和压力 (Edge angle and pressure)
            3. 上身姿势和平衡 (Upper body posture and balance)
            
            请用 **简体中文 (Simplified Chinese)** 提供分析结果。
            
            请提供以下 JSON 格式的输出：
            {
                "overall_technique_score": (1-10 之间的数字),
                "key_observations": ["观察点 1", "观察点 2"],
                "technical_advice": "详细的改进建议...",
                "frame_by_frame_analysis": [
                    {"frame_index": 0, "comment": "转弯入弯阶段..."},
                    ...
                ]
            }
            """}
        ]

        # Add images
        for frame_file in display_frames:
            full_path = os.path.join(frames_dir, frame_file)
            base64_image = encode_image_base64(full_path)
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/jpeg;base64,{base64_image}"
                }
            })

        logger.info(f"Sending {len(display_frames)} frames to OpenAI model {self.model_name}...")
        
        response = client.chat.completions.create(
            model=self.model_name,
            messages=[{"role": "user", "content": content}],
            response_format={ "type": "json_object" }
        )

        try:
            return json.loads(response.choices[0].message.content)
        except Exception as e:
            logger.error(f"Failed to parse JSON: {e}")
            return {"error": "Failed to parse analysis result", "raw": response.choices[0].message.content}


def get_coach(model_name: str) -> SkiCoachAI:
    if "gpt" in model_name.lower():
        return OpenAICoach(model_name)
    else:
        # Default to Gemini
        return GeminiCoach(model_name)

# --- Main Entry Point ---

def process_video(video_path, temp_dir, model_name=None):
    """
    Main entry point.
    """
    # 1. Determine Model
    if not model_name:
        model_name = os.getenv("AI_MODEL_NAME", "gemini-3-flash-preview")
    
    # Num frames
    try:
        num_frames = int(os.getenv("NUM_FRAMES_TO_EXTRACT", 10))
    except ValueError:
        num_frames = 10

    # 2. Extract Frames (Always try to extract for UI, and OpenAI needs them)
    job_id = os.path.basename(temp_dir)
    app_root = os.path.dirname(os.path.abspath(__file__))
    static_frames_dir = os.path.join(app_root, "static", "frames", job_id)
    
    logger.info(f"Extracting frames to {static_frames_dir}")
    display_frames = extract_frames_for_display(video_path, static_frames_dir, num_frames=num_frames)
    
    # 3. Get Coach and Analyze
    coach = get_coach(model_name)
    logger.info(f"Using Coach: {type(coach).__name__} with model {model_name}")
    
    result = coach.analyze(video_path, frames_dir=static_frames_dir, display_frames=display_frames)
    
    # Attach display frames to result for UI
    if isinstance(result, dict):
        result['display_frames'] = display_frames
        
    return result

if __name__ == "__main__":
    pass
