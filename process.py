from numpy import array, asarray, clip, float32, uint8
from torch import cuda
from easyocr import Reader
from PIL.Image import Image, open, Resampling, fromarray
from PIL.ImageOps import grayscale, autocontrast
from PIL.ImageFilter import GaussianBlur
from PIL.ImageEnhance import Contrast
from cv2 import (
    MORPH_CLOSE,
    MORPH_RECT,
    THRESH_BINARY_INV,
    THRESH_OTSU,
    filter2D,
    findNonZero,
    getStructuringElement,
    minAreaRect,
    morphologyEx,
    threshold as thresholder,
)


class Process:
    def __init__(
        self,
        resize_scale: int = 2,
        contrast_factor: float = 2.0,
        background_blur_radius: int = 25,
        auto_rotate: bool = True,
        auto_sharpen: bool = True,
    ) -> None:
        using_graphics_card = cuda.is_available()

        self.__reader = Reader(["vi", "en"], gpu=using_graphics_card)
        self.__resize_scale = resize_scale
        self.__contrast_factor = contrast_factor
        self.__background_blur_radius = background_blur_radius
        self.__auto_rotate = auto_rotate
        self.__auto_sharpen = auto_sharpen


    def readtext(self, image: Image) -> str:
        image_np = array(image)
        detections = self.__reader.readtext(image=image_np, detail=1)

        if not detections:
            return ""

        # detections item: (bbox, text, confidence); bbox là 4 điểm góc.
        items = []
        for bbox, text, _confidence in detections:
            xs = [point[0] for point in bbox]
            ys = [point[1] for point in bbox]
            center_y = sum(ys) / len(ys)
            height = max(ys) - min(ys)
            items.append(
                {
                    "text": text,
                    "x": min(xs),
                    "y": center_y,
                    "h": max(height, 1.0),
                }
            )

        # Sắp xếp thô theo y để việc gom dòng bên dưới diễn ra tuần tự.
        items.sort(key=lambda item: item["y"])

        lines: list[list[dict]] = []
        for item in items:
            placed = False

            for line in lines:
                ref = line[0]
                # Cùng 1 dòng nếu tâm y lệch nhau không quá nửa chiều cao chữ.
                if abs(item["y"] - ref["y"]) <= max(item["h"], ref["h"]) * 0.6:
                    line.append(item)
                    placed = True
                    break

            if not placed:
                lines.append([item])

        lines.sort(key=lambda line: sum(i["y"] for i in line) / len(line))

        rendered_lines = []
        for line in lines:
            line.sort(key=lambda item: item["x"])
            rendered_lines.append(" ".join(item["text"] for item in line))

        return "\n".join(rendered_lines)

    # Cải thiện độ nét
    def __sharpen(self, image: Image) -> Image:
        gray = grayscale(image)

        kernel = array(
            [
                [0, -1, 0],
                [-1, 5, -1],
                [0, -1, 0],
            ],
            dtype=float32,
        )

        image_np = array(gray)
        image_np = filter2D(image_np, -1, kernel)

        return fromarray(image_np)

    # Xóa nền bằng cách làm mờ nền, chuẩn hóa độ sáng rồi tăng tương phản ảnh.
    def __remove(self, image: Image) -> Image:
        gray = grayscale(image)

        background = gray.filter(GaussianBlur(radius=self.__background_blur_radius))

        gray_np = asarray(gray).astype("float32")
        bg_np = asarray(background).astype("float32")

        # Tránh lỗi chia cho 0 khi chuẩn hóa ảnh theo nền.
        bg_np[bg_np == 0] = 1

        normalized = gray_np / bg_np * 255
        normalized = clip(normalized, 0, 255).astype(uint8)

        removed = fromarray(normalized)
        removed = autocontrast(removed)
        removed = Contrast(removed).enhance(min(self.__contrast_factor, 1.5))

        return removed

    # Tự động tìm góc xoay tốt nhất để làm thẳng dòng chữ trong ảnh.
    def __rotate(self, image: Image) -> Image:
        max_angle = 15.0

        # Dùng ảnh nhỏ để xác định góc nghiêng, tránh xử lý trực tiếp trên ảnh lớn.
        score_source = image.copy()
        score_source.thumbnail((900, 900), Resampling.BILINEAR)

        gray = grayscale(score_source)
        gray = autocontrast(gray)
        gray_np = asarray(gray)

        # Tạo ma trận nhị phân 2D: chữ màu trắng, nền màu đen.
        _, binary_np = thresholder(
            gray_np,
            0,
            255,
            THRESH_BINARY_INV + THRESH_OTSU,
        )

        # Nối các pixel chữ gần nhau lại để OpenCV dễ xác định vùng chữ.
        kernel = getStructuringElement(MORPH_RECT, (30, 5))
        binary_np = morphologyEx(binary_np, MORPH_CLOSE, kernel)

        # Lấy tọa độ tất cả pixel chữ trong ma trận 2D.
        points = findNonZero(binary_np)

        if points is None:
            return image

        # Tìm hình chữ nhật nhỏ nhất bao quanh vùng chữ và lấy góc nghiêng.
        rect = minAreaRect(points)
        angle = float(rect[-1])

        # Chuẩn hóa góc trả về của OpenCV về góc cần xoay ảnh.

        if angle < -45:
            rotate_angle = -(90 + angle)
        elif angle > 45:
            rotate_angle = 90 - angle
        else:
            rotate_angle = -angle

        # Nếu góc quá lớn thì bỏ qua để tránh xoay sai ảnh.
        if abs(rotate_angle) > max_angle:
            return image

        image = image.rotate(
            angle=rotate_angle,
            resample=Resampling.BICUBIC,
            expand=True,
            fillcolor=(255, 255, 255),
        )

        return image

    # Tiền xử lý ảnh theo mode: raw, background, gray, contrast hoặc binary.
    def modify(
        self, image_path: str, mode: str, threshold: int, name: str | None
    ) -> Image:
        with open(fp=image_path) as source:
            image = source.convert("RGB")

        if mode == "raw":
            self.__save(image=image, name=name, mode=mode, threshold=threshold)
            return image

        resize = self.__resize_scale
        width, height = image.size
        image = image.resize(
            (width * resize, height * resize),
            Resampling.LANCZOS,
        )

        if self.__auto_rotate:
            image = self.__rotate(image)

        if mode == "background":
            image = self.__remove(image)

        if self.__auto_sharpen:
            image = self.__sharpen(image)

            return image

        image = grayscale(image)

        image = Contrast(image).enhance(min(self.__contrast_factor, 1.5))

        if self.__auto_sharpen:
            image = self.__sharpen(image)

        return image
