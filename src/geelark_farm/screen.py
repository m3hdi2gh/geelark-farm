"""See the phone's screen, and act on what is actually there.  [phase 2]

Responsibility:
- capture the live view hierarchy (`uiautomator dump` + `cat`) as XML
- parse it into simple rows: text, content-desc, class, clickable, bounds
- find an element by label, matching BOTH text and content-desc (GeeLark's own
  flows fail because they only match content-desc)
- tap by computing the centre of an element's bounds
- save a capture as a test fixture, so screen-matching logic can be unit
  tested against real screens without booting a phone

Element lookups are observed, never guessed. That distinction is the reason
this project exists.
"""
