"""Create a cute bunny icon for HopperDock"""
from pathlib import Path

from PIL import Image, ImageDraw

def create_bunny_icon():
    # Create multiple sizes for ICO
    sizes = [256, 128, 64, 48, 32, 16]
    images = []

    for size in sizes:
        img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Scale factor
        s = size / 256

        # Colors - neon pink theme
        pink = (255, 46, 151)  # #ff2e97
        pink_dark = (204, 37, 121)
        white = (255, 255, 255)
        bg_color = (30, 30, 30)  # Dark grey background

        # Draw circular background
        margin = int(10 * s)
        draw.ellipse([margin, margin, size - margin, size - margin], fill=bg_color)

        # Bunny ears (two tall ovals)
        ear_width = int(35 * s)
        ear_height = int(90 * s)
        ear_y = int(30 * s)

        # Left ear
        left_ear_x = int(70 * s)
        draw.ellipse([left_ear_x, ear_y, left_ear_x + ear_width, ear_y + ear_height], fill=pink)
        # Inner ear
        inner_margin = int(8 * s)
        draw.ellipse([left_ear_x + inner_margin, ear_y + int(15*s),
                      left_ear_x + ear_width - inner_margin, ear_y + ear_height - int(15*s)],
                     fill=pink_dark)

        # Right ear
        right_ear_x = int(150 * s)
        draw.ellipse([right_ear_x, ear_y, right_ear_x + ear_width, ear_y + ear_height], fill=pink)
        # Inner ear
        draw.ellipse([right_ear_x + inner_margin, ear_y + int(15*s),
                      right_ear_x + ear_width - inner_margin, ear_y + ear_height - int(15*s)],
                     fill=pink_dark)

        # Bunny face (large circle)
        face_size = int(140 * s)
        face_x = int(58 * s)
        face_y = int(90 * s)
        draw.ellipse([face_x, face_y, face_x + face_size, face_y + face_size], fill=pink)

        # Eyes (white circles with dark pupils)
        eye_size = int(25 * s)
        eye_y = int(130 * s)

        # Left eye
        left_eye_x = int(95 * s)
        draw.ellipse([left_eye_x, eye_y, left_eye_x + eye_size, eye_y + eye_size], fill=white)
        # Pupil
        pupil_size = int(12 * s)
        pupil_offset = int(7 * s)
        draw.ellipse([left_eye_x + pupil_offset, eye_y + pupil_offset,
                      left_eye_x + pupil_offset + pupil_size, eye_y + pupil_offset + pupil_size],
                     fill=bg_color)

        # Right eye
        right_eye_x = int(140 * s)
        draw.ellipse([right_eye_x, eye_y, right_eye_x + eye_size, eye_y + eye_size], fill=white)
        # Pupil
        draw.ellipse([right_eye_x + pupil_offset, eye_y + pupil_offset,
                      right_eye_x + pupil_offset + pupil_size, eye_y + pupil_offset + pupil_size],
                     fill=bg_color)

        # Nose (small pink triangle/oval)
        nose_x = int(120 * s)
        nose_y = int(165 * s)
        nose_size = int(18 * s)
        draw.ellipse([nose_x, nose_y, nose_x + nose_size, nose_y + int(12*s)], fill=pink_dark)

        # Cute smile (arc)
        smile_bbox = [int(105*s), int(170*s), int(155*s), int(200*s)]
        draw.arc(smile_bbox, 0, 180, fill=white, width=max(1, int(3*s)))

        # Whiskers
        whisker_color = white
        whisker_width = max(1, int(2 * s))
        # Left whiskers
        draw.line([(int(70*s), int(175*s)), (int(100*s), int(170*s))], fill=whisker_color, width=whisker_width)
        draw.line([(int(65*s), int(185*s)), (int(100*s), int(180*s))], fill=whisker_color, width=whisker_width)
        # Right whiskers
        draw.line([(int(160*s), int(170*s)), (int(190*s), int(175*s))], fill=whisker_color, width=whisker_width)
        draw.line([(int(160*s), int(180*s)), (int(195*s), int(185*s))], fill=whisker_color, width=whisker_width)

        images.append(img)

    # Save as ICO with multiple sizes, alongside this script
    out_path = Path(__file__).parent / 'hopper.ico'
    images[0].save(
        out_path,
        format='ICO',
        sizes=[(s, s) for s in sizes],
        append_images=images[1:]
    )
    print(f"Icon created: {out_path}")

if __name__ == '__main__':
    create_bunny_icon()
