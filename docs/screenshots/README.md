# A02 visual verification

The A02 screenshots were captured from the local edit route with representative ambiguous
characters, punctuation, decimals, status and fill controls, and multiline notes.

- `a02-edit-font-desktop.jpg` and `a02-edit-font-iphone.jpg`: basic fields and unchanged buttons.
- `a02-edit-font-desktop-select-placeholder.jpg` and
  `a02-edit-font-iphone-select-placeholder.jpg`: an empty placeholder and the Type select.
- `a02-edit-font-desktop-values.jpg` and `a02-edit-font-iphone-values.jpg`: status, fill output,
  storage punctuation, and decimal prices.
- `a02-edit-font-desktop-notes.jpg` and `a02-edit-font-iphone-notes.jpg`: multiline notes and
  representative punctuation.
- `a02-edit-font-fallback-desktop.jpg` and `a02-edit-font-fallback-iphone.jpg`: missing-font
  fallback rendering.

The desktop viewport was 1280×720. The iPhone viewport was 390×844; its document and form widths
were both 390px, confirming no horizontal overflow. Browser font checks confirmed the regular and
bold `AtkinsonEdit` faces loaded. Computed styles confirmed edit values use `AtkinsonEdit`, while
Save and analysis buttons continue to use `BookSans`. The same browser audit separately confirmed
that the Type select and placeholder pseudo-elements resolve to `AtkinsonEdit` on both viewports.

For the missing-font check, the application was served from a temporary copy with both WOFF2 files
intentionally omitted. Requests for the regular and bold assets returned 404, the browser exposed no
loaded custom font faces, and the controls rendered with the declared local stack
`Arial, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`. The 1280px desktop and 390px
iPhone layouts remained readable; the iPhone document width stayed at 390px with no horizontal
overflow. The focused static regression test also locks the fallback stack in place.
