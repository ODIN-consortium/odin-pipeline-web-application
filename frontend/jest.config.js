/** @type {import('jest').Config} */
module.exports = {
  preset: 'jest-preset-angular',
  setupFilesAfterEnv: ['<rootDir>/setup-jest.ts'],
  testEnvironment: 'jsdom',
  transform: {
    '^.+\\.(ts|js|mjs|html|svg)$': [
      'jest-preset-angular',
      {
        tsconfig: '<rootDir>/tsconfig.spec.json',
        stringifyContentPathRegex: '\\.html$',
      },
    ],
  },
  transformIgnorePatterns: ['node_modules/(?!.*\\.mjs$)'],
  testMatch: ['**/src/**/*.spec.ts'],
  moduleNameMapper: {
    '^rxjs/(.*)$': '<rootDir>/node_modules/rxjs/$1',
  },
  coverageDirectory: 'coverage',
  collectCoverageFrom: ['src/app/**/*.ts', '!src/app/**/*.spec.ts'],
};
