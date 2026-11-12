# Changelog

## 1.0.1

- Fix "'HTMLGenerator' object has no attribute 'javascript'" on Checkmk 3.0:
  the page no longer uses `html.javascript()`, which 3.0 removed. The auto
  reload during a running analysis uses Checkmk's own
  `immediate_browser_redirect()`; the search field gets its focus via
  `autofocus`.
- Fix "register() missing 1 required keyword-only argument: 'debug'" on
  Checkmk 3.0: the analysis run (mcactl) passes the new `debug` argument to
  `main_modules.register()` only where Checkmk expects it.
- Fix "MCA setup user rules applied: 0 rule(s) - error: ImportError(cannot
  import name 'folder_tree' ...)" on newer Checkmk 3.0 builds, which replaced
  `folder_tree()` with `make_folder_tree(config)`: the page now uses whichever
  of the two exists.

## 1.0.0

- Initial release.
