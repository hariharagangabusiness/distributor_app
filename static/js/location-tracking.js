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
(function () {
  if (!window.LOCATION_TRACKING_ENABLED || !navigator.geolocation) return;

  var PING_INTERVAL_MS = 30 * 60 * 1000; // 30 minutes
  var WORK_START_HOUR = 9;
  var WORK_END_HOUR = 20;   // 8 PM
  var CLOSED_DAY = 1;       // Date.getDay(): Sunday=0 ... Monday=1 - the one day off

  function withinWorkingHours() {
    var d = new Date();
    return d.getDay() !== CLOSED_DAY && d.getHours() >= WORK_START_HOUR && d.getHours() < WORK_END_HOUR;
  }

  function sendPing() {
    if (!withinWorkingHours()) return;
    navigator.geolocation.getCurrentPosition(function (pos) {
      fetch('/api/location-ping', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          lat: pos.coords.latitude,
          lng: pos.coords.longitude,
          accuracy: pos.coords.accuracy,
        }),
      }).catch(function () {
        // Offline or a transient network error - simply skipped, the next
        // scheduled attempt 30 minutes later will try again.
      });
    }, function () {
      // Permission denied or position unavailable - nothing to send this
      // round; the browser will keep asking (or not) per its own policy on
      // subsequent attempts.
    }, { enableHighAccuracy: false, timeout: 20000, maximumAge: 5 * 60 * 1000 });
  }

  // Capture once shortly after the page loads (so a login mid-shift starts a
  // trail immediately rather than waiting up to 30 min for the first point),
  // then every 30 minutes for as long as this tab remains open.
  setTimeout(sendPing, 3000);
  setInterval(sendPing, PING_INTERVAL_MS);
})();
