import { setHeadlessWhen, setCommonPlugins } from '@codeceptjs/configure';
// turn on headless mode when running with HEADLESS=true environment variable
// export HEADLESS=true && npx codeceptjs run
setHeadlessWhen(process.env.HEADLESS);
const testRoot = process.env.TEST_ROOT || '.';

// enable all common plugins https://github.com/codeceptjs/configure#setcommonplugins
setCommonPlugins();

function browserConfig() {
  if (process.env.CHROMIUM) {
    return {
      browser: 'chromium',
      chromium: {
        executablePath: process.env.CHROMIUM,
      },
    }
  } else if (process.env.FIREFOX) {
    return {
      browser: 'firefox',
      firefox: {
        executablePath: process.env.FIREFOX,
      },
    }
  } else {
    throw Error('Invalid browser config. Specify path to your browser in either CHROMIUM or FIREFOX environment variable.')
  }
}

/** @type {CodeceptJS.MainConfig} */
export const config = {
  tests: ['/**/*.test.js', '/**/*_test.js', '/**/test.js'].map(x => testRoot + x),
  output: './output',
  helpers: {
    Playwright: {
      ...browserConfig(),
      url: 'https://localhost',
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