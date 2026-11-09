import test from 'node:test';
import assert from 'node:assert/strict';
import {observationText,probabilities,percent,observationExperiments,recordExperimentId} from '../src/observationView.js';

test('experiment options prefer active registration and preserve legacy groups',()=>{
 const records=[{experiment_id:'old',created_at:'2026-11-09T10:00:00Z'},{experiment_id:'new',created_at:'2026-11-09T12:00:00Z'},{experiment_id:'old',created_at:'2026-11-09T11:00:00Z'}];
 const choices=observationExperiments({active_experiment_id:'new',experiments:[{id:'old',created_at:'2026-11-08T10:00:00Z'},{id:'new',created_at:'2026-11-09T10:00:00Z'}],observations:records});
 assert.deepEqual(choices.map(e=>e.id),['new','old']);assert.equal(choices[0].active,true);assert.equal(choices[1].active,false);
 assert.equal(observationExperiments({observations:records}).length,2);
 assert.equal(observationExperiments({observations:[{id:'legacy'}]})[0].id,recordExperimentId({}));
 const emptyCurrent=observationExperiments({active_experiment_id:'future',observations:records});assert.equal(emptyCurrent[0].id,'future');
});
test('observation distinguishes failure, missing and unsupported market',()=>{
 assert.equal(observationText(null,'had'),'待观察');assert.equal(observationText({status:'missed'},'had'),'错过时点');
 assert.equal(observationText({status:'completed',probabilities:{had:{h:.6,d:.2,a:.2}}},'hhad'),'不支持');
 assert.equal(percent(null),'—');assert.equal(percent(0),'0.0%');
});
test('nested detail and row forecasts retain signed handicap and probabilities',()=>{
 const r={status:'completed',result:{handicap:1,probabilities:{hhad:{h:.4,d:.5,a:.1}}}};
 assert.equal(observationText(r,'hhad'),'(+1) 让平 50.0%');assert.equal(probabilities(r,'hhad').length,3);
});
test('multi selection uses frozen keys and total-goal option labels',()=>{
 const r={status:'completed',probabilities:{ttg:{s2:.3,s3:.25,s7:.1}},selections:{ttg:['s2','s3']}};
 assert.equal(observationText(r,'ttg'),'2球 / 3球 55.0%');
 assert.equal(observationText({...r,selections:{ttg:[]}},'ttg'),'观望');
 assert.equal(probabilities({probabilities:{had:{h:NaN,d:-1,a:1.1}}},'had').length,0);
});
