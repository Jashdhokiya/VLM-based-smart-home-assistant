import requests
import json
import cv2
import uuid
import os
from dotenv import load_dotenv

load_dotenv()

# Module-level storage for fresh metadata
current_metadata = None

def annotate(objects, image_path):
    global current_metadata
    url = "https://api.va.landing.ai/v1/tools/text-to-object-detection"
    owlvit_api_key = os.getenv("OWLVIT_API_KEY", "")

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image file '{image_path}' does not exist for annotation.")

    # 1. If valid Landing AI key is configured, use OWLv2
    if owlvit_api_key and owlvit_api_key not in ("...", "") and not owlvit_api_key.startswith("YOUR_"):
        print(f"[DETECTION] Calling Landing AI OWLv2 with prompts={objects} on {image_path}...")
        try:
            with open(image_path, "rb") as image_file:
                files = {"image": image_file}
                data = {"prompts": objects, "model": "owlv2"}
                headers = {"Authorization": "Basic " + owlvit_api_key}
                response = requests.post(url, files=files, data=data, headers=headers, timeout=12)

            if response.status_code == 200:
                response_data = response.json()
                if "data" in response_data and response_data["data"]:
                    current_metadata = response_data
                    with open("metadata.py", "w", encoding="utf-8") as f:
                        f.write(f"metadata = {json.dumps(response_data, indent=4)}\n")
                    print("[DETECTION] Landing AI response saved in metadata.py.")
                    return current_metadata
            print(f"[DETECTION] [WARN] Landing AI returned {response.status_code}. Falling back to visual layout.")
        except Exception as e:
            print(f"[DETECTION] [WARN] Landing AI request failed: {e}. Falling back to visual layout.")

    # 2. Single-Key Groq Mode: Generate left-to-right candidate bounding boxes
    print("[DETECTION] Groq single-key mode: Generating detection candidate layout...")
    img = cv2.imread(image_path)
    if img is not None:
        h, w = img.shape[:2]
    else:
        h, w = 720, 1280

    items = []
    # Distribute candidate boxes across the image
    slots = 2
    for label in (objects if objects else ["light"]):
        for i in range(slots):
            box_w = int(w * 0.22)
            box_h = int(h * 0.35)
            x1 = int(w * (0.15 + i * 0.45))
            x2 = min(w - 10, x1 + box_w)
            y1 = int(h * 0.30)
            y2 = min(h - 10, y1 + box_h)
            items.append({
                "label": label,
                "score": round(0.98 - i * 0.02, 2),
                "bounding_box": [x1, y1, x2, y2],
                "id": str(uuid.uuid4())
            })

    response_data = {"data": [items]}
    current_metadata = response_data
    try:
        with open("metadata.py", "w", encoding="utf-8") as f:
            f.write(f"metadata = {json.dumps(response_data, indent=4)}\n")
        print("[DETECTION] Visual layout saved. You can adjust boxes in the GUI window or auto-accept.")
    except Exception as e:
        print(f"[DETECTION] Warning: Could not write metadata.py: {e}")

    return current_metadata

def parser(label_counts, meta=None):
    """Filters metadata to retain only the top-N scoring objects per label."""
    active_meta = meta or current_metadata
    if active_meta is None:
        if os.path.exists("metadata.py"):
            try:
                with open("metadata.py", "r", encoding="utf-8") as f:
                    content = f.read()
                    if "metadata =" in content:
                        dict_str = content.split("metadata =", 1)[1].strip()
                        active_meta = json.loads(dict_str)
            except Exception:
                pass

    if not active_meta or "data" not in active_meta:
        print("[DETECTION] [ERROR] No valid metadata found.")
        return {"data": []}

    data_field = active_meta["data"]
    if not isinstance(data_field, list) or not data_field:
        print("[DETECTION] [WARN] Metadata data field is empty.")
        return {"data": []}

    all_items = data_field[0] if isinstance(data_field[0], list) else data_field

    for item in all_items:
        if "id" not in item:
            item["id"] = str(uuid.uuid4())

    def get_top_uuids_by_score(items, counts):
        top_uuids = {}
        label_items = {}

        for item in items:
            lbl = item["label"]
            if lbl not in label_items:
                label_items[lbl] = []
            label_items[lbl].append(item)

        for lbl, count in counts.items():
            if lbl in label_items:
                sorted_items = sorted(label_items[lbl], key=lambda x: x["score"], reverse=True)
                top_uuids[lbl] = [it["id"] for it in sorted_items[:count]]

        return top_uuids

    top_uuids = get_top_uuids_by_score(all_items, label_counts)
    new_metadata = {"data": [[]]}

    for lbl, uuids in top_uuids.items():
        filtered_items = [
            {key: value for key, value in item.items() if key != "id"}
            for item in all_items
            if item["label"] == lbl and item["id"] in uuids
        ]
        new_metadata["data"][0].extend(filtered_items)

    return new_metadata

# Global variables for dragging
dragging = False
selected_box = None
offset_x = 0
offset_y = 0

def visualize(image_path, new_metadata):
    global dragging, selected_box, offset_x, offset_y

    plotted_objects = []
    image = cv2.imread(image_path)
    if image is None:
        raise FileNotFoundError(f"Cannot read image at {image_path} for visualization.")

    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    all_detections = new_metadata["data"]
    colors = {"fan": (0, 255, 0), "light": (0, 255, 0)}

    bounding_boxes = []
    label_dict = {}

    for detections in all_detections:
        for obj in detections:
            lbl = obj["label"]
            if lbl not in label_dict:
                label_dict[lbl] = []
            label_dict[lbl].append(obj)

    # Assign sequential naming sorted left to right (light1, light2...)
    for lbl, detections in label_dict.items():
        detections.sort(key=lambda obj: (obj["bounding_box"][0], obj["bounding_box"][1]))
        for idx, obj in enumerate(detections):
            x1, y1, x2, y2 = map(int, obj["bounding_box"])
            label_with_suffix = f"{lbl}{idx + 1}"
            bounding_boxes.append({
                "label": label_with_suffix,
                "box": [x1, y1, x2, y2],
                "color": colors.get(lbl.lower(), (0, 255, 255))
            })
            plotted_objects.append(label_with_suffix)

    # Render bounding boxes onto image
    annotated = image.copy()
    for box in bounding_boxes:
        x1, y1, x2, y2 = box["box"]
        lbl = box["label"]
        color = box["color"]
        cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
        text_y = max(y1 - 5, 10)
        cv2.putText(annotated, lbl, (x1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
        cv2.putText(annotated, lbl, (x1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    auto_accept = os.getenv("ANNOTATION_AUTO_ACCEPT", "false").lower() == "true"
    if auto_accept:
        print("[DETECTION] Auto-accept enabled. Saving annotated_image.jpg directly.")
        cv2.imwrite("annotated_image.jpg", cv2.cvtColor(annotated, cv2.COLOR_RGB2BGR))
        return "accept", plotted_objects

    # Interactive GUI mode
    instructions = "Press 'r' to Refresh, 'm' for Manual, 'a' to Accept | Drag boxes with mouse"

    def on_mouse(event, x, y, flags, param):
        global dragging, selected_box, offset_x, offset_y
        if event == cv2.EVENT_LBUTTONDOWN:
            for b in bounding_boxes:
                bx1, by1, bx2, by2 = b["box"]
                if bx1 <= x <= bx2 and by1 <= y <= by2:
                    selected_box = b
                    dragging = True
                    offset_x = x - bx1
                    offset_y = y - by1
                    break
        elif event == cv2.EVENT_MOUSEMOVE:
            if dragging and selected_box:
                bx1, by1, bx2, by2 = selected_box["box"]
                w = bx2 - bx1
                h = by2 - by1
                selected_box["box"] = [x - offset_x, y - offset_y, x - offset_x + w, y - offset_y + h]
        elif event == cv2.EVENT_LBUTTONUP:
            dragging = False
            selected_box = None

    cv2.namedWindow("Annotated Image")
    cv2.setMouseCallback("Annotated Image", on_mouse)

    while True:
        temp_image = image.copy()
        for b in bounding_boxes:
            bx1, by1, bx2, by2 = b["box"]
            lbl = b["label"]
            clr = b["color"]
            cv2.rectangle(temp_image, (bx1, by1), (bx2, by2), clr, 2)
            text_y = max(by1 - 5, 10)
            cv2.putText(temp_image, lbl, (bx1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 3)
            cv2.putText(temp_image, lbl, (bx1, text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, clr, 2)

        display_image = temp_image.copy()
        cv2.putText(display_image, instructions, (10, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.imshow("Annotated Image", cv2.cvtColor(display_image, cv2.COLOR_RGB2BGR))

        key = cv2.waitKey(1) & 0xFF
        if key == ord("r"):
            print("[DETECTION] Refreshing annotations...")
            cv2.destroyAllWindows()
            return "refresh", plotted_objects
        elif key == ord("m"):
            print("[DETECTION] Manual annotation mode...")
            return "manual", plotted_objects
        elif key == ord("a"):
            print("[DETECTION] Accepting and saving image.")
            cv2.imwrite("annotated_image.jpg", cv2.cvtColor(temp_image, cv2.COLOR_RGB2BGR))
            cv2.destroyAllWindows()
            return "accept", plotted_objects

def user_choice(image_path, objects, label_counts):
    while True:
        new_metadata = parser(label_counts)
        action, plotted_objects = visualize(image_path, new_metadata)

        if action == "refresh":
            annotate(objects, image_path)
        elif action == "manual":
            print("Drag and drop functionality: use the mouse to move bounding boxes on the window.")
        elif action == "accept":
            return plotted_objects

def start_annot(objects, image_path, label_counts):
    annotate(objects, image_path)
    return user_choice(image_path, objects, label_counts)