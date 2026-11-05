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
      this.section('Install source');
      this.click('Models');
      this.usePlaywrightTo('Prepare clipboard permissions/content', async ({ browserContext, page }) => {
        await browserContext.grantPermissions(['clipboard-read', 'clipboard-write']);
        await page.evaluate(`navigator.clipboard.writeText("${btoa(JSON.stringify([src]))}")`);
      });
      this.click({ id: 'add-model' });
      this.click('Ok');
      this.waitForNavigation({ timeout: 5000 });
    },

    deleteGroup() {
      this.amOnPage('/models?tour=1');
      this.moveCursorTo('.model-group');
      this.click('button[onclick*="remove"]');
      this.click('Ok');
    },

    deleteModel() {
      this.amOnPage('/models?tour=1');
      this.click('button[onclick*="delete_"]', 'a.model');
      this.click('Ok');
      this.waitForNavigation(); // wait for page refresh
    },

    async externalDataset(filename, manifest) {
      this.section('Loading external dataset');
      this.click('button[onclick*="external"]');
      this.attachFile('#dataset-upload', filename); // filename relative to test root
      this.wait(3); // wait a bit for file to load
      this.click('Ok');
      this.waitForElement('.loading');
      // If error for dataset exists is shown
      if (await E.tryTo(() => this.waitForElement('.toast.error'))) {
          this.click('button[onclick*="external"]');
          this.selectOption('.dropdown select', manifest);
          this.click('Ok');
      }
      this.waitForURL(url => url.pathname.startsWith('/model/'), { timeout: 5000 });
    },

    training(options, timeout) {
      this.section('Configure training');
      for (const args of options) {
          this.modelOption(...args);
      }

      this.section('Training');
      this.click('#model-training');
      this.waitForURL(url => url.pathname.startsWith('/task/logs'));
      this.waitForElement('#action-training', timeout);
      this.click('#action-training');
    },

    async inference(handler) {
      this.section('Inference');
      this.click('#model-inference');
      this.waitForElement('.toast');
      this.waitForInvisible('.toast', 5);
      this.startRecordingTraffic();
      await E.hopeThat(handler);
      this.stopRecordingTraffic();
      const traffics = await this.grabRecordedNetworkTraffics();
      this.click('#status');
      this.waitForURL(url => url.pathname == '/task/status');
      this.click('h2:first-of-type+a');
      this.waitForURL(url => url.pathname.startsWith('/task/logs/'));
      this.click('Stop task');
      this.click('Yes');
      this.waitForNavigation(); // wait for page refresh
      E.hopeThat.noErrors();
      const preds = traffics.find(({ url }) => new URL(url).pathname.startsWith('/infer/'))?.response?.body;
      this.assert(preds !== undefined);
      return preds;
    },

    modelOption(name, type, value) {
      this.click('.add-option');
      this.click(name, { css: '#list-options' });
      switch (type) {
        case 'text': this.fillField('#confirmation input', value); break;
        case 'enum': this.click(value, '#confirmation'); break;
        default: throw Error('Invalid option type');
      }
      this.click('Ok');
    }

  });
}
