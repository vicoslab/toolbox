// in this file you can append custom step methods to 'I' object
import { actor } from 'codeceptjs';
import { Section } from 'codeceptjs/steps';
import * as E from 'codeceptjs/effects'
import { ok } from 'node:assert/strict';

export default function() {
  return actor({
    assert(condition, message) {
      ok(condition, message);
    },

    section(title) {
      Section(title);
    },

    installSrc (src) {
      Section("Install source");
      this.click('Models');
      this.usePlaywrightTo('Prepare clipboard permissions/content', async ({ browserContext, page }) => {
        await browserContext.grantPermissions(['clipboard-read', 'clipboard-write']);
        await page.evaluate(`navigator.clipboard.writeText("${btoa(JSON.stringify([src]))}")`);
      });
      this.click({ id: 'add-model' });
      this.click('Ok');
      this.waitForURL(url => url.pathname == '/models');
    },

    tryTo(handler) {
      return E.tryTo(handler);
    },

    modelOption(name, type, value) {
      this.click('.add-option');
      this.click(name, { css: '#list-options' });
      switch (type) {
        case 'text': this.fillField('#confirmation input', value); break;
        default: throw Error('Invalid option type');
      }
      this.click('Ok');
    }
    // Define custom steps here, use 'this' to access default methods of I.
    // It is recommended to place a general 'login' function here.

  });
}
