// The console's one script, which lived inside a Python string until
// 2026-09-20 and so was invisible to every tool this project runs. That
// is how `var typing` came to be computed in `mayRedraw` and never read:
// the guard that holds a redraw while somebody is typing was dead from
// September, under a green test asserting `"function mayRedraw()" in
// script`. A substring cannot see an unused variable.
//
//     npx eslint
//
export default [
  {
    files: ["src/geelark_farm/web/static/dash.js"],
    languageOptions: {
      ecmaVersion: 2020,
      sourceType: "script",
      globals: {
        window: "readonly", document: "readonly", location: "writable",
        history: "readonly", navigator: "readonly", console: "readonly",
        setTimeout: "readonly", clearTimeout: "readonly", fetch: "readonly",
        FormData: "readonly", URLSearchParams: "readonly", URL: "readonly",
        DOMParser: "readonly", EventSource: "readonly", Event: "readonly",
        Option: "readonly", HTMLFormElement: "readonly", MouseEvent: "readonly",
        addEventListener: "readonly", innerHeight: "readonly",
        innerWidth: "readonly", sessionStorage: "readonly",
      },
    },
    rules: {
      // The one that would have caught it.
      "no-unused-vars": ["error", {args: "none", caughtErrors: "none"}],
      "no-undef": "error",
      "no-unreachable": "error",
      "no-dupe-keys": "error",
      "no-dupe-args": "error",
      "no-self-assign": "error",
      "no-constant-condition": "error",
      "no-fallthrough": "error",
    },
  },
];
