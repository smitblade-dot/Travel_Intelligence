# Nuclear UI regression and cleanup review

PASS for local UI candidate index SHA256 `eb16420014586341bea80b29f70dd170900d191b4850868ab105e025fd1431e8` and test SHA256 `96f1c9a5711114b4cdeaab600ed7be1638618a12f4c70a6c95931e264a496b8f`.

Independently ran the test script successfully. Added assertions execute the actual home/country/navigation functions: labelled nuclear home heading, nuclear directory entry, nuclear return label and destination, generic country return label/destination, and hidden entry for unpublished inventory. Existing source date, URL, publication, country isolation, legacy profile and pagination checks still pass.

Unused nuclearProfile assignments are absent from the current index. Initial review found the stale generator insertion and patch; owner corrected both, and independent recheck confirms neither still inserts this property. Publication filters and navigation behavior remain as previously reviewed. Exact uploaded PR head and external QA remain the parent's final verification step; no new browser check or deployment is claimed here.
