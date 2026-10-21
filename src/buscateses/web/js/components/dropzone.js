/** Drag-and-drop upload area with a regular file picker fallback. */

import { h } from "../dom.js";
import { icon } from "../icons.js";

export const LIBRARY_ACCEPT = ".pdf,.epub,.docx,.txt,.md,.html,.htm";

/**
 * @param {{ onFiles: (files: File[]) => void, accept?: string, title?: string }} options
 */
export function createDropzone({
  onFiles,
  accept = LIBRARY_ACCEPT,
  title = "Arraste seus arquivos para cá",
}) {
  const input = h("input", {
    type: "file",
    multiple: true,
    accept,
    class: "sr-only",
    tabindex: "-1",
    "aria-label": "Escolher arquivos para a biblioteca",
  });

  const chooseButton = h(
    "button",
    { class: "btn btn--primary", type: "button" },
    icon("upload", { size: 18 }),
    h("span", {}, "Escolher arquivos"),
  );
  chooseButton.addEventListener("click", () => input.click());

  input.addEventListener("change", () => {
    const files = [...(input.files ?? [])];
    if (files.length) onFiles(files);
    input.value = "";
  });

  const dropzone = h(
    "section",
    {
      class: "dropzone",
      "aria-labelledby": "dropzone-title",
      "aria-describedby": "dropzone-help",
    },
    icon("file", { size: 28 }),
    h("h2", { id: "dropzone-title" }, title),
    h(
      "p",
      { id: "dropzone-help" },
      "PDF, EPUB, Word, texto, Markdown ou HTML, até 500 MB por arquivo.",
    ),
    h("div", { class: "dropzone__actions" }, chooseButton),
    input,
  );

  for (const name of ["dragenter", "dragover"]) {
    dropzone.addEventListener(name, (event) => {
      event.preventDefault();
      dropzone.classList.add("dropzone--over");
    });
  }
  for (const name of ["dragleave", "dragend"]) {
    dropzone.addEventListener(name, () => dropzone.classList.remove("dropzone--over"));
  }
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    dropzone.classList.remove("dropzone--over");
    const files = [...(event.dataTransfer?.files ?? [])];
    if (files.length) onFiles(files);
  });

  return dropzone;
}
