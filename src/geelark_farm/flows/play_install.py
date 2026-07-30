"""Install a package from the Play Store and prove it landed.  [phase 5]

Already solved in the prototype; this is the known-good sequence:

- deep link straight to the package page:
  `am start -a android.intent.action.VIEW -d "market://details?id=<pkg>"`
  No text search, so no chance of installing a clone.
- tap Install by matching text OR content-desc (on the Play page the label is
  a TextView with clickable=false; tapping its centre works anyway)
- keep clearing interstitials WHILE polling. On a fresh account the chain
  appears after the Install tap, not before:
      "Complete account setup" -> Continue
      -> "Add a payment option" -> Skip
      -> Play Pass promo -> Not now
      -> the download finally starts
  The chain is account-level: once skipped it does not return, so later phones
  on the same account install in about 30 seconds.
- success is `pm list packages <pkg>` returning the package. Nothing else.
"""
