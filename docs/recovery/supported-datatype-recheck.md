# Supported data types recheck

PASS for staged content after mechanical correction, 2026-09-29. Frontend DATA_TYPES (index.html:367) explicitly includes CONTACT and CONSIDERATION, not GUIDANCE. All15 emergency records now CONTACT and all52 finance records CONSIDERATION. Reversing only dataType changes recovers every previously reviewed exact file hash, so sourced content and observations are unchanged. This corrects an earlier QA gap: schema validators had accepted a value the frontend does not support.

- EMERGENCY.json: `20cd721e5627d8cb79f44b6fb6c77ed8f6825758dbceae6f35412d74317557ab`; prior-source-reviewed bytes recovered by reversing dataType: True.
- FINANCE.json: `6fb075a92ca7d6e2378402c55d7515e4a70ed6cabeaa3aa83c1806082f2f2655`; prior-source-reviewed bytes recovered by reversing dataType: True.
- FINANCE_02.json: `ceddde88728675c122f5a6203ec7bb702df44aed7395d7301029729bc2b1b40a`; prior-source-reviewed bytes recovered by reversing dataType: True.
- FINANCE_03.json: `d38de5581f834a3b457b66974546962932f471862f9c178bbb661f717866f19a`; prior-source-reviewed bytes recovered by reversing dataType: True.
- FINANCE_04.json: `0d9bbb9007dcd80be3f6d24f33979fe98d542e9bfca50ea67dd3d8c3da8c552b`; prior-source-reviewed bytes recovered by reversing dataType: True.
- FINANCE_05.json: `97feb61ba573080936adf4c70cf3d3422635abbfda668acdf912ef3ef196c2e0`; prior-source-reviewed bytes recovered by reversing dataType: True.
- FINANCE_06.json: `bee79ece523b2f7d39b5cbc5fb0ac63e71e6c402db5519327aab60ff83bdbcc7`; prior-source-reviewed bytes recovered by reversing dataType: True.
- FINANCE_07.json: `fd311d5a9506f30c8a482e2c3e69acdd7253ff4f5157261f98ce74cd66eb8ce5`; prior-source-reviewed bytes recovered by reversing dataType: True.

This addendum supersedes file hashes in individual batch reports. Full integrated validation, independent PR verdict and strict release gates remain required.
