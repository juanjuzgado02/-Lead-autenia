import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{js,jsx}'],
    extends: [
      js.configs.recommended,
      // 'recommended-latest' is the flat-config export in eslint-plugin-react-hooks 5.x.
      // (`configs.flat.recommended` only exists in 6.x and threw on load.)
      reactHooks.configs['recommended-latest'],
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
      parserOptions: {
        ecmaVersion: 'latest',
        ecmaFeatures: { jsx: true },
        sourceType: 'module',
      },
    },
    rules: {
      // caughtErrors defaulted to 'none' in ESLint 8 and to 'all' in 9. This
      // codebase is written against the old default (`catch (e) {}` to swallow
      // an error is used deliberately), so keep it explicit rather than
      // annotating every call site.
      'no-unused-vars': ['error', {
        varsIgnorePattern: '^[A-Z_]',
        caughtErrors: 'none',
      }],
    },
  },
  {
    // Build tooling runs in Node, not the browser.
    files: ['*.config.js', 'vite-plugin-*.js', 'seo/**/*.js'],
    languageOptions: { globals: globals.node },
  },
])
