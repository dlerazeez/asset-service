import os


def guess_extension(filename: str | None, content_type: str | None) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext:
        return ext
    if content_type:
        ct = content_type.lower()
        if "pdf" in ct:
            return ".pdf"
        if "png" in ct:
            return ".png"
        if "jpeg" in ct or "jpg" in ct:
            return ".jpg"
    return ".bin"
