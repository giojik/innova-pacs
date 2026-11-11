// Innova: ZIP-ის გადმოწერის ღილაკი OHIF 3.13 viewer-ში (nginx sub_filter-ით ემატება index.html-ს).
// გაზიარება/ZIP პორტალის worklist-შია; ღილაკი fixed პოზიციითაა, OHIF-ის DOM-ზე არ არის დამოკიდებული.
(function () {
  var ID = 'innova-zip-btn';
  function sync() {
    var onViewer = window.location.pathname.indexOf('/viewer') === 0;
    var uid = new URLSearchParams(window.location.search).get('StudyInstanceUIDs');
    var btn = document.getElementById(ID);
    if (!onViewer || !uid) {
      if (btn) btn.remove();
      return;
    }
    if (!btn) {
      btn = document.createElement('button');
      btn.id = ID;
      btn.type = 'button';
      btn.textContent = '📥 ZIP';
      btn.title = 'კვლევის გადმოწერა (ZIP)';
      btn.style.cssText =
        'position:fixed;left:12px;bottom:12px;z-index:9999;background:#28a745;color:#fff;' +
        'border:none;border-radius:6px;padding:6px 12px;font:bold 13px sans-serif;cursor:pointer;' +
        'box-shadow:0 2px 6px rgba(0,0,0,.4);opacity:.9';
      document.body.appendChild(btn);
    }
    btn.onclick = function (e) {
      e.preventDefault();
      var current = new URLSearchParams(window.location.search).get('StudyInstanceUIDs') || '';
      window.open('/p/download-zip/' + encodeURIComponent(current.split(',')[0]) + '/auto', '_blank');
    };
  }
  setInterval(sync, 1500);
  sync();
})();
