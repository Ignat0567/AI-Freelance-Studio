from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "frontend" / "build" / "branding" / "ai-freelance-studio-source.png"
TRANSPARENT_SOURCE = SOURCE.with_name("icon-source-transparent.png")
ICON = ROOT / "frontend" / "build" / "icon.ico"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def remove_checkerboard(image: Image.Image) -> Image.Image:
    rgba = image.convert("RGBA")
    cleaned = []
    for red, green, blue, _ in rgba.getdata():
        distance = ((255 - red) ** 2 + (255 - green) ** 2 + (255 - blue) ** 2) ** 0.5
        alpha = max(0, min(255, round((distance - 12) * 255 / 58)))
        if alpha == 0:
            cleaned.append((0, 0, 0, 0))
            continue
        if alpha < 255:
            factor = 255 / alpha
            red = max(0, min(255, round(255 - (255 - red) * factor)))
            green = max(0, min(255, round(255 - (255 - green) * factor)))
            blue = max(0, min(255, round(255 - (255 - blue) * factor)))
        cleaned.append((red, green, blue, alpha))
    rgba.putdata(cleaned)
    return rgba


def emblem_canvas(image: Image.Image) -> Image.Image:
    emblem = image.crop((70, 190, 1185, 985))
    alpha_box = emblem.getchannel("A").getbbox()
    if not alpha_box:
        raise RuntimeError("Brand asset did not produce a visible transparent emblem")
    emblem = emblem.crop(alpha_box)
    canvas = Image.new("RGBA", (1024, 1024), (0, 0, 0, 0))
    emblem.thumbnail((920, 920), Image.Resampling.LANCZOS)
    canvas.alpha_composite(emblem, ((1024 - emblem.width) // 2, (1024 - emblem.height) // 2))
    return canvas


def main() -> None:
    source = Image.open(SOURCE)
    canvas = emblem_canvas(remove_checkerboard(source))
    canvas.save(TRANSPARENT_SOURCE, "PNG", optimize=True)
    canvas.save(ICON, "ICO", sizes=[(size, size) for size in ICON_SIZES])
    print(f"Created {ICON} with sizes: {', '.join(map(str, ICON_SIZES))}")


if __name__ == "__main__":
    main()
