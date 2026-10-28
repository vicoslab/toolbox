import { Section } from 'codeceptjs/steps';

Feature('Manage model group')

Before(({ I }) => {
  I.amOnPage('/');
});

Scenario('SuperSimpleNet', ({ I }) => {
  Section('Open the models list');
  I.click('Models');

  Section('Add model group');
  I.installSrc({
    owner: 'TestInstall',
    group: 'TestGroup',
    models: ['super-simple-net'],
    url: 'https://github.com/vicoslab/toolbox-models',
    branch: 'dev'
  });
  I.see('SuperSimpleNet', { css: 'a.model' });

  const version = '2430d46';
  Section('Pin group version');
  I.moveCursorTo('.model-group');
  I.click({ css: 'button[onclick*="update"]'});
  I.click('Version override');
  I.fillField('Git revision', version);
  I.click('Ok');
  I.waitForNavigation();
  I.moveCursorTo('.model-group');
  I.see(version);

  Section('Update pinned group');
  I.moveCursorTo('.model-group');
  I.click({ css: 'button[onclick*="update"]'});
  I.click('Ok');
  I.waitForNavigation();
  I.moveCursorTo('.model-group');
  I.see(version);

  Section('Unpin group version');
  I.moveCursorTo('.model-group');
  I.click({ css: 'button[onclick*="update"]'});
  I.click('Version override');
  I.fillField('Git revision', 'origin/HEAD');
  I.click('Ok');
  I.waitForNavigation();
  I.moveCursorTo('.model-group');
  I.dontSee(version);

  Section('Delete group');
  I.moveCursorTo('.model-group');
  I.click({ css: 'button[onclick*="remove"]'});
  I.click('Ok');
  I.dontSee('SuperSimpleNet');
});
