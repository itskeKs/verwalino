"""Render synthetic mail previews and full-page Chromium captures, without a DB."""

from pathlib import Path
import sys

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from playwright.sync_api import sync_playwright

# Also support direct invocation from the repository root.
BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.mail import render_mail
from app.mail.contexts import CONTEXT_TYPES
from app.mail.samples import SAMPLES


def main() -> None:
    output = BACKEND / "build" / "mail-previews"
    output.mkdir(parents=True, exist_ok=True)
    if SAMPLES.keys() != CONTEXT_TYPES.keys():
        raise ValueError("Every context needs exactly one preview sample")
    previews = []
    for mode in ("hosted", "selfhost"):
        for kind, sample in SAMPLES.items():
            stem = kind if mode == "hosted" else f"{kind}-selfhost"
            rendered = render_mail(sample, deployment_mode=mode)
            for suffix, content in (("html", rendered.html), ("txt", rendered.text)):
                (output / f"{stem}.{suffix}").write_text(content, encoding="utf-8")
            previews.append({"stem": stem, "mode": mode, "subject": rendered.subject, "text": rendered.text})

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            context = browser.new_context(device_scale_factor=1)
            # Preview content must never load network resources, even by accident.
            context.route("http://**/*", lambda route: route.abort())
            context.route("https://**/*", lambda route: route.abort())
            page = context.new_page()
            for preview in previews:
                for width in (600, 375):
                    for scheme in ("light", "dark"):
                        page.set_viewport_size({"width": width, "height": 800})
                        page.emulate_media(color_scheme=scheme)
                        page.goto((output / f"{preview['stem']}.html").as_uri(), wait_until="load")
                        if page.evaluate("document.documentElement.scrollWidth") > width:
                            raise ValueError(f"Horizontal overflow in {preview['stem']} at {width}px")
                        page.screenshot(path=str(output / f"{preview['stem']}-{width}-{scheme}.png"), full_page=True)
        finally:
            browser.close()

    environment = Environment(loader=FileSystemLoader(Path(__file__).parent),
                              autoescape=select_autoescape(("html",)), undefined=StrictUndefined)
    gallery = environment.get_template("mail_preview_gallery.html").render(previews=previews)
    (output / "index.html").write_text(gallery, encoding="utf-8")
    print(f"Rendered {len(previews)} mail previews and {len(previews) * 4} PNGs: {output / 'index.html'}")


if __name__ == "__main__":
    main()
