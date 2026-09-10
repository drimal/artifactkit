"""Renders PresentationSpec to .pptx via python-pptx.

Layout resolution: Slide.layout is matched by name against the
template's slide_layouts (case-insensitive). If no template is given,
python-pptx's default template is used and layout falls back to index
lookup against its five standard layouts.

Theming: PresentationSpec.theme, when set, is applied per slide after
placeholders are filled. Title-type slides ("title" / "section_header"
layouts) get a gradient hero background with light title text and a
decorative corner shape -- the bold, attention-grabbing treatment a
human designer would give an opening/section slide. Every other slide
gets a clean, readable solid background with a thin accent bar --
content slides are for reading, not for competing with the content.
This mirrors how real decks differentiate title slides from content
slides, rather than applying one flat look to every slide uniformly.

All of this is pure python-pptx: solid/gradient fills, font
color/size/weight, and preset auto-shapes. No external rendering
engine (no pptxgenjs, no Node runtime) -- this backend has to run
unattended inside deployed agent infrastructure that may not have
Node available, so the styling ceiling here is "everything python-pptx
can natively do," not "everything a JS rendering library can do."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.util import Inches, Pt

from artifactkit.core.backend import ValidationResult
from artifactkit.core.models import ArtifactSpec, PresentationSpec, Theme

_DEFAULT_LAYOUT_NAMES = {
    "title": 0,
    "title_and_body": 1,
    "section_header": 2,
    "two_content": 3,
    "title_only": 5,
    "blank": 6,
}

# Layout names treated as "hero" slides (gradient background, decorative
# shape) rather than "content" slides (light background, accent bar).
_HERO_LAYOUTS = {"title", "section_header"}


@dataclass(frozen=True)
class _ThemeStyle:
    # Hero slides (title / section_header layouts): gradient background.
    gradient_start_hex: str
    gradient_end_hex: str
    gradient_angle: float
    hero_text_hex: str  # title text color on the gradient — must read on dark
    decorative_hex: str  # corner shape color on hero slides

    # Content slides (everything else): solid background, accent bar.
    content_background_hex: str
    content_title_hex: str
    body_hex: str
    accent_hex: str

    font_name: str
    title_size_pt: int = 40


# Concrete colors/fonts per preset live here, not on Theme
# itself — the enum is spec data (what the agent asked for), this
# mapping is a rendering detail specific to PptxBackend.
_THEME_STYLES: dict[Theme, _ThemeStyle] = {
    Theme.VIBRANT: _ThemeStyle(
        gradient_start_hex="E94560",
        gradient_end_hex="533483",
        gradient_angle=45.0,
        hero_text_hex="FFFFFF",
        decorative_hex="0F3460",
        content_background_hex="FFFFFF",
        content_title_hex="E94560",
        body_hex="16213E",
        accent_hex="F0A500",
        font_name="Calibri",
        title_size_pt=40,
    ),
    Theme.CORPORATE: _ThemeStyle(
        gradient_start_hex="1F3A5F",
        gradient_end_hex="4A90D9",
        gradient_angle=90.0,
        hero_text_hex="FFFFFF",
        decorative_hex="2C5F8A",
        content_background_hex="FFFFFF",
        content_title_hex="1F3A5F",
        body_hex="333333",
        accent_hex="4A90D9",
        font_name="Georgia",
        title_size_pt=36,
    ),
    Theme.MINIMAL: _ThemeStyle(
        gradient_start_hex="1A1A1A",
        gradient_end_hex="3D3D3D",
        gradient_angle=135.0,
        hero_text_hex="FFFFFF",
        decorative_hex="CCCCCC",
        content_background_hex="FFFFFF",
        content_title_hex="1A1A1A",
        body_hex="444444",
        accent_hex="999999",
        font_name="Helvetica",
        title_size_pt=34,
    ),
}


class PptxBackend:
    def render(self, spec: ArtifactSpec, output_path: Path) -> None:
        assert isinstance(spec, PresentationSpec)
        presentation = (
            Presentation(spec.template_path) if spec.template_path else Presentation()
        )
        layouts_by_name = {layout.name.lower(): layout for layout in presentation.slide_layouts}
        style = _THEME_STYLES.get(spec.theme) if spec.theme else None

        for slide_spec in spec.slides:
            layout = self._resolve_layout(presentation, layouts_by_name, slide_spec.layout)
            slide = presentation.slides.add_slide(layout)
            self._fill_placeholders(slide, slide_spec.placeholders)
            if style is not None:
                is_hero = slide_spec.layout.lower() in _HERO_LAYOUTS
                self._apply_theme(slide, presentation, style, is_hero)
            for image in slide_spec.images:
                kwargs = {"width": Inches(image.width_inches)} if image.width_inches else {}
                slide.shapes.add_picture(image.source_path, Inches(0.5), Inches(0.5), **kwargs)
            if slide_spec.speaker_notes:
                slide.notes_slide.notes_text_frame.text = slide_spec.speaker_notes

        output_path.parent.mkdir(parents=True, exist_ok=True)
        presentation.save(output_path)

    def _resolve_layout(self, presentation, layouts_by_name: dict, name: str):
        key = name.lower()
        if key in layouts_by_name:
            return layouts_by_name[key]
        if key in _DEFAULT_LAYOUT_NAMES:
            index = _DEFAULT_LAYOUT_NAMES[key]
            if index < len(presentation.slide_layouts):
                return presentation.slide_layouts[index]
        # Fall back to the first layout rather than raising: an agent
        # requesting an unknown layout name shouldn't lose the whole slide.
        return presentation.slide_layouts[0]

    def _fill_placeholders(self, slide, placeholders: dict[str, str]) -> None:
        placeholder_by_type = {ph.placeholder_format.type: ph for ph in slide.placeholders}
        remaining = dict(placeholders)

        # Prefer matching by placeholder name/idx convention: "title" and
        # "body" are handled explicitly since they're the overwhelming
        # majority case; anything else fills placeholders positionally
        # in declaration order.
        if "title" in remaining and slide.shapes.title is not None:
            slide.shapes.title.text = remaining.pop("title")

        other_placeholders = [ph for ph in slide.placeholders if ph != slide.shapes.title]
        for ph, (_, text) in zip(other_placeholders, remaining.items()):
            ph.text_frame.text = text

    def _apply_theme(self, slide, presentation, style: _ThemeStyle, is_hero: bool) -> None:
        if is_hero:
            self._apply_hero_background(slide, presentation, style)
            title_hex = style.hero_text_hex
        else:
            self._apply_content_background(slide, presentation, style)
            title_hex = style.content_title_hex

        if slide.shapes.title is not None:
            for paragraph in slide.shapes.title.text_frame.paragraphs:
                for run in paragraph.runs:
                    run.font.color.rgb = RGBColor.from_string(title_hex)
                    run.font.name = style.font_name
                    run.font.bold = True
                    run.font.size = Pt(style.title_size_pt)

        if not is_hero:
            for ph in slide.placeholders:
                if ph == slide.shapes.title:
                    continue
                for paragraph in ph.text_frame.paragraphs:
                    for run in paragraph.runs:
                        run.font.color.rgb = RGBColor.from_string(style.body_hex)
                        run.font.name = style.font_name

    def _apply_hero_background(self, slide, presentation, style: _ThemeStyle) -> None:
        background = slide.background
        background.fill.gradient()
        stops = background.fill.gradient_stops
        stops[0].color.rgb = RGBColor.from_string(style.gradient_start_hex)
        stops[0].position = 0.0
        stops[1].color.rgb = RGBColor.from_string(style.gradient_end_hex)
        stops[1].position = 1.0
        background.fill.gradient_angle = style.gradient_angle

        # A large circle mostly off-slide in the bottom-right corner —
        # the "peeking shape" motif common in modern deck design. Only
        # a quarter of it is actually visible.
        diameter = Inches(4)
        decorative = slide.shapes.add_shape(
            MSO_SHAPE.OVAL,
            presentation.slide_width - Inches(2),
            presentation.slide_height - Inches(2),
            diameter,
            diameter,
        )
        decorative.fill.solid()
        decorative.fill.fore_color.rgb = RGBColor.from_string(style.decorative_hex)
        decorative.line.fill.background()
        decorative.shadow.inherit = False

    def _apply_content_background(self, slide, presentation, style: _ThemeStyle) -> None:
        background = slide.background
        background.fill.solid()
        background.fill.fore_color.rgb = RGBColor.from_string(style.content_background_hex)

        # Thin accent bar down the left edge — the one decorative touch
        # on a content slide, kept minimal so it doesn't compete with
        # whatever text/data the slide is actually there to convey.
        accent = slide.shapes.add_shape(
            MSO_SHAPE.RECTANGLE, Inches(0), Inches(0), Inches(0.15), presentation.slide_height
        )
        accent.fill.solid()
        accent.fill.fore_color.rgb = RGBColor.from_string(style.accent_hex)
        accent.line.fill.background()
        accent.shadow.inherit = False

    def validate(self, output_path: Path, spec: ArtifactSpec) -> ValidationResult:
        assert isinstance(spec, PresentationSpec)
        errors: list[str] = []
        checks: list[str] = []
        try:
            presentation = Presentation(output_path)
            checks.append("file_opens")
        except Exception as exc:
            return ValidationResult(is_valid=False, errors=(f"unreadable: {exc}",))

        if len(presentation.slides) != len(spec.slides):
            errors.append(
                f"expected {len(spec.slides)} slides, found {len(presentation.slides)}"
            )
        else:
            checks.append("slide_count")

        return ValidationResult(is_valid=not errors, checks_passed=tuple(checks), errors=tuple(errors))
