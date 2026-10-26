import { setHeadlessWhen, setCommonPlugins } from '@codeceptjs/configure';
// turn on headless mode when running with HEADLESS=true environment variable
// export HEADLESS=true && npx codeceptjs run
setHeadlessWhen(process.env.HEADLESS);

// enable all common plugins https://github.com/codeceptjs/configure#setcommonplugins
setCommonPlugins();

/** @type {CodeceptJS.MainConfig} */
export const config = {
  tests: './*.test.js',
  output: './output',
  helpers: {
    Playwright: {
      browser: 'chromium',
      url: 'https://localhost',
      chromium: {
        ...process.env.CHROMIUM && { executablePath: process.env.CHROMIUM }
      },
      ignoreHTTPSErrors: true,
      show: true
    }
  },
  include: {
    I: './steps_file.js'
  },
  noGlobals: true,
  plugins: {},
  name: 'frontend'
}