# Nuclear UI back-button label recheck

PASS for the local staged label correction. Replacing only the new conditional label with the prior All countries label reproduces previously reviewed index hash d2acd9dcb3ffb32299c65585b8c6045f891ca68b8bd42dd105b8811595722581 exactly. The click handler and all gates/routes are unchanged. Independent UI unit tests pass. No new browser-render claim; exact uploaded PR head and required CI/Claude review remain before merge.

- index.html: `c107481e18fb334ab99b97ae85f65cb6ea149f39bbba17de1b9e0b457f36d746`
- build_ui.py: `e9cb869aca664f83cd527672926d1fbdeaea59b7aa7f8ab3e10b07f83b574e88`
- nuclear-ui.patch: `db63d688196104294f17d378545fe3c0cbe49ac9deace657fa50cbceaa886060`
