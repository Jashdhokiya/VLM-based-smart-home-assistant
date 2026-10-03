# 🧠 INOT Demo – Intelligence of Things

This repository provides a runnable demo of the **Intelligence of Things (INOT)** (Mark 1) system, integrating vision, language, spatial reasoning, and smart device control. The system is designed for real-time interaction in smart environments.

---

## 🚀 Demo Overview

This demo showcases:

- Vision-based object detection using OWL-ViT
- Spatial inference from annotated scenes
- Natural language processing and command execution
- Smart device actuation using Tuya API
- Real-time voice command transcription

---
## 🎥 Live Demo
Complex commands start after 0:58

[![INOT Demo Video](https://img.youtube.com/vi/4i5PMCkPSew/0.jpg)](https://www.youtube.com/watch?v=4i5PMCkPSew)
---

## 🛠️ Setup Instructions

### 1. Clone the Repository

```bash
git clone https://github.com/intelligenceofthings/Mark1.git
cd your-repo
```

### 2. Install Dependencies

### Environment Variables

Create a .env file in the root directory with the following keys:

```env
GROQ_API_KEY=your_groq_api_key
OPENAI_API_KEY=your_openai_api_key
GEMINI_API_KEY=your_gemini_api_key
TUYA_EMAIL=your_tuya_email
TUYA_PWD=your_tuya_password
COUNTRY_CODE=your_country_code  # e.g., 91 for India
IMAGE_PATH=path_to_scene_image.jpg  # e.g., images/scene.jpg
OWLVIT_API_KEY=your_owlvit_api_key
```

## 📸 Preparing Your Demo Scene

1. Place an image of the scene (e.g., a room with smart devices) in the `images/` directory.
2. Start the program by

```bash
   python main.py
```

## Smart Home Control Pipeline

The pipeline includes the following modules:

### 1. `nl_processor.py`

- Prompts the user to list out the devices present in the environment using natural language.
- **Input**: User's natural language description.
- **Output**: Structured list of detected devices.

### 2. `onboarder.py`

- Automatically annotates the scene using inputs from the user.
- **Output**: Generates `annotated_image.jpg` with labeled devices.

### 3. `spatial_inferencer.py`

- Constructs a textual interpretation of the annotated scene.
- **Output**: Descriptive, spatially aware text about device locations.

### 4. `transcriptor.py`

- Converts user speech into text, supporting multiple languages.
- **Input**: Multilingual speech command.
- **Output**: Transcribed command in text format.

### 5. `command_processor.py`

- Evaluates the textual command and determines which device should be turned on or off.
- **Function**: Parses command context and intent for actuation.

### 6. `tuya_executor.py`

- Serves as a wrapper to connect the Gemini reasoning module with the Tuya Smart Device API.
- **Function**: Executes device control actions via Tuya API.


## 🌐 Project Page

Detailed system overview, architecture, and vision:  
https://ontelligency.sukanthoriginal.com/intelligenceofthings
