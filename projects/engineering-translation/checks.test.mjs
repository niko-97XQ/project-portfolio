import test from 'node:test';
import assert from 'node:assert/strict';
import {checkTranslation} from '../../docs/engineering-translation/checks.mjs';
import {checkCompleteness,checkVisualCoverage} from '../../docs/engineering-translation/page-completeness.mjs';
test('same engineering dimension passes; changed value or unit is flagged',()=>{const s='Pile diameter 600 mm';assert.deepEqual(checkTranslation(s,'桩径 600 mm'),[]);for(const t of ['桩径 60 mm','桩径 600 m'])assert.ok(checkTranslation(s,t).length)});
test('an unwritten translation is missing even when target text exists',()=>{const r=checkCompleteness([{id:'a',source:'Pile diameter',target:'桩径',disposition:'translate'}],[]);assert.deepEqual(r.completeness.missingIds,['a'])});
test('recognized rows cannot establish whole-page coverage',()=>assert.notEqual(checkVisualCoverage([]).status,'complete'));
