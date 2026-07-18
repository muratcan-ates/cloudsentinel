/* Boot script for the self-hosted Swagger UI (static/docs.html).
 * Lives in its own file because the dashboard-wide CSP is script-src 'self':
 * an inline <script> — which FastAPI's default docs page relies on — would
 * never execute here. */
window.addEventListener("load", function () {
  window.ui = SwaggerUIBundle({
    url: "/openapi.json",
    dom_id: "#swagger-ui",
    deepLinking: true,
    presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset].filter(Boolean),
    layout: "BaseLayout",
  });
});
