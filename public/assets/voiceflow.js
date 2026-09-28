// Voiceflow chat widget for the client-facing pages. Admin pages
// (admin.html, admin-login.html) deliberately don't include this file.
(function (d, t) {
  var VOICEFLOW_PROJECT_ID = "6abad3602dff24a1e246486e";
  var v = d.createElement(t), s = d.getElementsByTagName(t)[0];
  v.onload = function () {
    window.voiceflow.chat.load({
      verify: { projectID: VOICEFLOW_PROJECT_ID },
      url: "https://general-runtime.voiceflow.com",
      voice: { url: "https://runtime-api.voiceflow.com" }
    });
  };
  v.src = "https://cdn.voiceflow.com/widget-next/bundle.mjs";
  v.type = "text/javascript";
  s.parentNode.insertBefore(v, s);
})(document, "script");
