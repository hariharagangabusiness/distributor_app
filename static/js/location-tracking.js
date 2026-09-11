// Captures this device's location every 30 minutes and sends it to the
// server, for as long as this tab stays open on a tracked-role login (Staff/
// Supervisor/Manager - see base.html, which only includes this file for
// those roles). Only fires during working hours (9 AM-8 PM, Tuesday-Sunday,
// by the DEVICE's own clock/day - the server independently re-checks its
// own clock before storing anything, so a wrong device clock can only cause
// a missed or extra attempt, never a bad write).
//
// Limits, by the nature of a website (not a native app): this can only run
// while the tab is open and in a browser that's still executing JS for it -
// closing the tab, force-quitting the browser, or the phone locking/sleeping
// for long enough to suspend the tab all stop tracking until it's reopened.
// There is no way for a normal website to track location in the background.
//
// A small status pill (bottom-right) shows what's actually happening -
// capturing silently and failing silently is exactly how this kind of
// feature goes unnoticed as broken for weeks, so every outcome (success,
// permission denied, GPS timeout, outside working hours, server rejected
// it) is surfaced somewhere the person using the phone can see it and
// report back, instead of vanishing into a browser console nobody opens.
(function () {
  if (!window.LOCATION_TRACKING_ENABLED) return;

  var PING_INTERVAL_MS = 30 * 60 * 1000; // 30 minutes
  var WORK_START_HOUR = 9;
  var WORK_END_HOUR = 20;   // 8 PM
  var CLOSED_DAY = 1;       // Date.getDay(): Sunday=0 ... Monday=1 - the one day off

  var pill = null;
  function showStatus(text, kind) {
    if (!pill) {
      pill = document.createElement('div');
      pill.id = 'location-tracking-pill';
      pill.style.cssText = 'position:fixed;bottom:14px;right:14px;z-index:2000;' +
        'padding:.4rem .7rem;border-radius:999px;font-size:.75rem;font-family:sans-serif;' +
        'box-shadow:0 2px 8px rgba(0,0,0,.2);cursor:default;max-width:80vw;' +
        'transition:opacity .3s;opacity:0.9;';
      document.body.appendChild(pill);
    }
    var colors = {
      ok: '#1f5c4d', wait: '#607d8b', warn: '#c9971e', error: '#c0392b',
    };
    pill.style.background = colors[kind] || colors.wait;
    pill.style.color = '#fff';
    pill.textContent = '📍 ' + text;
    pill.style.opacity = '0.9';
  }

  function withinWorkingHours() {
    var d = new Date();
    return d.getDay() !== CLOSED_DAY && d.getHours() >= WORK_START_HOUR && d.getHours() < WORK_END_HOUR;
  }

  if (!navigator.geolocation) {
    showStatus('Location tracking not supported by this browser', 'error');
    return;
  }

  function sendPing() {
    if (!withinWorkingHours()) {
      showStatus('Location tracking paused (outside 9 AM–8 PM, Tue–Sun)', 'wait');
      return;
    }
    showStatus('Capturing location…', 'wait');
    navigator.geolocation.getCurrentPosition(function (pos) {
      fetch('/api/location-ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        }),
      }).then(function (r) { return r.json(); }).then(function (data) {
        if (data && data.ok) {
          var t = new Date();
          showStatus('Location sent ' + t.getHours() + ':' + String(t.getMinutes()).padStart(2, '0'), 'ok');
        } else {
          var reasons = {
            'not-tracked': "Server didn't recognize this login as tracked",
            'outside-hours': 'Server says outside working hours (check server clock/timezone)',
            'bad-coords': 'Device sent invalid coordinates',
          };
          showStatus('Not saved: ' + (reasons[(data && data.reason)] || (data && data.reason) || 'unknown reason'), 'warn');
        }
      }).catch(function () {
        showStatus('Could not reach server (offline?) — will retry next cycle', 'error');
      });
    }, function (err) {
      var messages = {
        1: 'Location permission denied — enable it in browser/site settings',
        2: 'Position unavailable — GPS/network location off?',
        3: 'Location request timed out',
      };
      showStatus(messages[err.code] || ('Location error: ' + err.message), 'error');
    }, { enableHighAccuracy: false, timeout: 20000, maximumAge: 5 * 60 * 1000 });
  }

  // Capture once shortly after the page loads (so a login mid-shift starts a
  // trail immediately rather than waiting up to 30 min for the first point),
  // then every 30 minutes for as long as this tab remains open.
  setTimeout(sendPing, 3000);
  setInterval(sendPing, PING_INTERVAL_MS);
})();
