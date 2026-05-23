import js from '@eslint/js';
import globals from 'globals';

export default [
    js.configs.recommended,
    {
        files: ['src/address_validator/static/admin/js/**/*.js'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: { ...globals.browser },
        },
    },
    {
        files: ['tests/js/**/*.js'],
        languageOptions: {
            ecmaVersion: 2022,
            sourceType: 'module',
            globals: { ...globals.browser },
        },
    },
    {
        ignores: ['node_modules/', 'skills-vendor/', 'skills/', '.venv/', 'dist/'],
    },
];
