# Interface design

## Selected direction

The phone and admin screens use the high-contrast yellow / cobalt concept
selected on 2026-09-27. It combines warm paper surfaces, near-black outlines,
hard offset shadows, bold sans-serif headings, and a compact palette:

- canvas: `#f6f4eb`; panels: `#fffef9`;
- primary highlight: `#ffe600`;
- interactive blue: `#064bff`;
- outline and text: `#111111`;
- green and red remain reserved for call and error status.

## Scope and behavior

Apply the same visual language to `/phone/` and `/admin/`, including forms,
device selection, directory tables, presence indicators, audio-device controls,
and incoming / active call dialogs. Preserve existing call flows, text meaning,
status semantics, and audio behavior. Keep layouts responsive and preserve
visible keyboard focus. Do not add new softphone functionality as part of a
visual redesign.

## Source files

- `admin-ui/src/design-system.css` — shared palette, typography, and focus.
- `admin-ui/src/phone/phone.css` — browser phone and call modal.
- `admin-ui/src/styles.css` — admin navigation, tables, forms, and metrics.
