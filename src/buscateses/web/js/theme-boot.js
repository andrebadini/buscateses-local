/* Apply the saved theme before the first paint (kept external for CSP). */
try {
  var savedTheme = localStorage.getItem("buscateses.theme");
  if (savedTheme === "light" || savedTheme === "dark") {
    document.documentElement.dataset.theme = savedTheme;
  }
} catch (error) {
  /* storage can be blocked; the system theme applies */
}
