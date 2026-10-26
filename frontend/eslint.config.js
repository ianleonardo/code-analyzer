import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    plugins: { "react-hooks": reactHooks },
    rules: {
      "react-hooks/rules-of-hooks": "error",
      // Native <select> pop-ups are drawn by the OS and ignore the app theme; use <Select> from components/ui.
      "no-restricted-syntax": ["error", { selector: "JSXOpeningElement[name.name='select']", message: "Use <Select> from components/ui so the dropdown follows the selected theme." }],
    },
  },
);
