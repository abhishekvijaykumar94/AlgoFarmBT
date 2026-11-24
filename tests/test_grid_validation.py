"""Grid candidates through the existing public validation command."""
import hashlib,json,unittest
import importlib.util
from pathlib import Path
from algoLibs.market_data.types import decode
import test_training_selection as selection_fixture


@unittest.skipUnless(importlib.util.find_spec('duckdb'),'Requires algoLibs[duckdb]')
class GridValidationTests(unittest.TestCase):
    setUp=selection_fixture.TrainingSelectionTests.setUp
    command=selection_fixture.TrainingSelectionTests.command
    study=selection_fixture.TrainingSelectionTests.study

    def inputs(self):
        selection_fixture.TrainingSelectionTests.inputs(self)
        self.plan['schema']='chronological-validation-v3'
        self.plan['selection'].pop('candidates')
        self.plan['selection']['search']=dict(method='grid',parameters=dict(smoothing_span=[3,6],signal_span=[6,9]))
        self.plan_path.write_text(json.dumps(self.plan))

    def test_grid_preview_executes_four_candidates_through_existing_duckdb_runner(self):
        self.inputs();self.database.close()
        output,report,preview=self.study('grid','--duckdb',self.path)
        self.assertEqual(preview['evaluation_count'],5)
        self.assertEqual({(c['recipe']['smoothing_span'],c['recipe']['signal_span']) for c in preview['candidates']},{(3,6),(3,9),(6,6),(6,9)})
        self.assertEqual([c['coordinates'] for c in preview['candidates']],[dict(signal_span=6,smoothing_span=3),dict(signal_span=6,smoothing_span=6),dict(signal_span=9,smoothing_span=3),dict(signal_span=9,smoothing_span=6)])
        selection=report['selections']['fold-001']
        self.assertEqual([t['status'] for t in selection['trials']],['succeeded']*4)
        self.assertEqual(report['folds'][0]['status'],'succeeded')
        self.assertIn(selection['selected_candidate'],{c['id'] for c in preview['candidates']})
        for candidate,trial in zip(preview['candidates'],selection['trials']):
            folder=output/'cases/fold-001/training'/trial['artifacts']/'run'
            self.assertEqual(trial['score'],decode(json.loads((folder/'report.json').read_text()))['net_return'])
            access=decode(json.loads((folder/'data-access.json').read_text()))
            self.assertEqual(access['route'],'DataRepository.query -> DataRequest.accept -> backend visitor')
            self.assertTrue(access['reads'])
            self.assertTrue(all(q['complete'] for q in access['reads']))
            self.assertEqual(candidate['id'],'grid-'+candidate['recipe_sha256'][:40])
            self.assertEqual(candidate['warmup_bars'],512)
        self.assertEqual(preview['plan']['selection']['search'],self.plan['selection']['search'])

    def test_grid_and_independently_declared_candidates_have_identical_finances(self):
        self.database.close()
        # Literal combinations are independently authored, not expanded by the resolver under test.
        for label,axes,combinations in [
            ('one',dict(smoothing_span=[3],signal_span=[6]),[(3,6)]),
            ('four',dict(smoothing_span=[3,6],signal_span=[6,9]),[(3,6),(6,6),(3,9),(6,9)])]:
            with self.subTest(label=label):
                self.inputs();self.plan['selection']['search']['parameters']=axes
                self.plan_path.write_text(json.dumps(self.plan))
                grid,grid_report,preview=self.study(label+'-grid','--duckdb',self.path)
                identities={(c['recipe']['smoothing_span'],c['recipe']['signal_span']):c['id'] for c in preview['candidates']}
                self.plan['selection'].pop('search')
                self.plan['selection']['candidates']=[dict(id=identities[(smooth,signal)],
                    recipe_overrides=dict(smoothing_span=smooth,signal_span=signal)) for smooth,signal in combinations]
                self.plan_path.write_text(json.dumps(self.plan))
                explicit,explicit_report,_=self.study(label+'-explicit','--duckdb',self.path)
                a=grid_report['selections']['fold-001'];b=explicit_report['selections']['fold-001']
                for key in ('status','selected_candidate','selected_recipe_sha256','training_data_cutoff'):
                    self.assertEqual(a[key],b[key],key)
                fields=('case_id','status','score','metrics','eligible','recipe_sha256')
                self.assertEqual([{k:t[k] for k in fields} for t in a['trials']],
                                 [{k:t[k] for k in fields} for t in b['trials']])
                self.assertEqual(grid_report['folds'][0]['status'],'succeeded')
                for run in list((grid/'cases/fold-001/training/cases').glob('*/run'))+[grid/'cases/fold-001/run']:
                    for name in ('result.json','spec.json','targets.json','target-timing.jsonl','signals.json','report.json'):
                        self.assertEqual((run/name).read_bytes(),(explicit/run.relative_to(grid)/name).read_bytes(),(label,run,name))

    def test_invalid_grid_coordinate_is_reported_before_market_reads(self):
        self.inputs()
        self.plan['selection']['search']['parameters']={'smoothing_span':[3,0]}
        self.plan_path.write_text(json.dumps(self.plan))
        self.prices=self.root/'missing-prices'
        with self.assertRaisesRegex(ValueError,r'Grid coordinates.*smoothing_span.*0.*positive integer'):
            self.command(self.root/'invalid','--dry-run')
        self.assertFalse((self.root/'invalid').exists())

    def test_grid_declarations_reject_invalid_searches_before_market_reads(self):
        self.inputs();original=json.loads(json.dumps(self.plan))
        self.prices=self.root/'missing-prices'
        invalid=[
            ('empty',dict(method='grid',parameters={}), 'nonempty'),
            ('empty-axis',dict(method='grid',parameters={'vol_target':[]}), 'nonempty'),
            ('scalar-axis',dict(method='grid',parameters={'vol_target':'.5'}), 'nonempty'),
            ('method',dict(method='random',parameters={'vol_target':['.5']}), 'grid method'),
            ('extra',dict(method='grid',parameters={'vol_target':['.5']},extra=True), 'grid method'),
            ('cardinality',dict(method='grid',parameters={'signal_span':list(range(1,34))}), 'cardinality is 33'),
            ('unknown',dict(method='grid',parameters={'unknown_parameter':[1]}), 'Grid coordinates.*unknown_parameter'),
            ('duplicate',dict(method='grid',parameters={'vol_target':['.50','.5']}), 'Grid coordinates.*Duplicate'),
            ('fixed',dict(method='grid',parameters={'annualisation_hours':[24]}), 'Grid coordinates.*comparison assumptions')]
        for name,search,error in invalid:
            with self.subTest(name=name):
                self.plan=json.loads(json.dumps(original));self.plan['selection']['search']=search
                self.plan_path.write_text(json.dumps(self.plan))
                with self.assertRaisesRegex(ValueError,error):self.command(self.root/name,'--dry-run')
                self.assertFalse((self.root/name).exists())
        for name,change in [('both',{'candidates':[dict(id='x',recipe_overrides={})]}),('neither',{})]:
            self.plan=json.loads(json.dumps(original));self.plan['selection'].pop('search')
            if name=='both':self.plan['selection']['search']=original['selection']['search']
            self.plan['selection'].update(change);self.plan_path.write_text(json.dumps(self.plan))
            with self.assertRaisesRegex(ValueError,'selection fields'):self.command(self.root/name,'--dry-run')
        self.plan=json.loads(json.dumps(original));self.plan['schema']='chronological-validation-v2'
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'selection fields'):self.command(self.root/'v2-grid','--dry-run')

    def test_v3_explicit_candidates_reject_equivalent_decimals_and_id_collisions(self):
        self.inputs();self.plan['selection'].pop('search');self.prices=self.root/'missing-prices'
        for name,candidates,error in [
            ('decimal',[dict(id='a',recipe_overrides={'vol_target':'.50'}),dict(id='b',recipe_overrides={'vol_target':'.5'})],'Duplicate'),
            ('ids',[dict(id='a',recipe_overrides={'vol_target':'.25'}),dict(id='a',recipe_overrides={'vol_target':'.5'})],'Unique safe')]:
            with self.subTest(name=name):
                self.plan['selection']['candidates']=candidates;self.plan_path.write_text(json.dumps(self.plan))
                with self.assertRaisesRegex(ValueError,error):self.command(self.root/name,'--dry-run')
                self.assertFalse((self.root/name).exists())

    def test_source_policy_incompatible_coordinate_fails_preview_before_repository_reads(self):
        from algoLibs.dao import DataRepository
        from algoLibs.market_data.types import encode
        from algoFarmBT.backtests.validation import run_validation
        from test_spot_inputs import T,H
        self.inputs()
        manifest_path=self.prices/'manifest.json';manifest=decode(json.loads(manifest_path.read_text()))
        manifest.update(format='qualified-spot-price-bars-v3',availability_policy='observed_hourly_preserve_state_v1',availability_exclusions=[])
        for entry in manifest['entries']:entry.update(first_observed=T,end=T+744*H,gaps=[])
        manifest_path.write_text(json.dumps(encode(manifest)))
        (self.prices/'manifest.sha256').write_text(hashlib.sha256(manifest_path.read_bytes()).hexdigest())
        self.plan['selection']['search']['parameters']={'rv_estimator':['ewma_zero_mean_variance','ewma_central_variance']}
        self.plan_path.write_text(json.dumps(self.plan))
        class DeniedRepository(DataRepository):
            def query(self,request,*args,**kwargs):raise PermissionError('repository policy denied')
        with self.assertRaisesRegex(ValueError,'Grid coordinates.*ewma_central_variance.*Elapsed-time gap policy'):
            run_validation(self.prices,self.spec,self.recipe,self.plan_path,self.root/'incompatible',
                           dry_run=True,data_repository=DeniedRepository())
        self.assertFalse((self.root/'incompatible').exists())

    def test_grid_preview_is_deterministic_and_frozen_execution_rejects_changed_grid(self):
        self.inputs();self.database.close()
        first=self.root/'first';self.command(first,'--dry-run','--duckdb',self.path)
        before=decode(json.loads((first/'resolved-plan.json').read_text()))
        self.assertEqual(before['inputs']['plan'],hashlib.sha256(self.plan_path.read_bytes()).hexdigest())
        self.assertEqual(len(before['candidate_windows']),4)
        from test_spot_inputs import T,H
        for window in before['candidate_windows']:
            self.assertEqual((window['training_history_start'],window['train_start'],window['train_end'],
                              window['test_history_start'],window['test_start'],window['test_end']),
                             (T+16*H,T+528*H,T+600*H,T+89*H,T+601*H,T+609*H))
        self.plan['selection']['search']['parameters']={'signal_span':[6,9],'smoothing_span':[3,6]}
        self.plan_path.write_text(json.dumps(self.plan))
        second=self.root/'second';self.command(second,'--dry-run','--duckdb',self.path)
        after=decode(json.loads((second/'resolved-plan.json').read_text()))
        self.assertEqual(before['candidates'],after['candidates'])
        self.plan['selection']['search']['parameters']['signal_span']=[6,12]
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'Frozen resolved plan or inputs changed'):
            self.command(self.root/'changed','--resolved-plan',first/'resolved-plan.json','--duckdb',self.path)
        self.assertFalse((self.root/'changed').exists())

    def test_grid_preview_rejects_repository_denial_and_price_mismatch_without_fallback(self):
        from algoLibs.dao import DataRepository
        from algoFarmBT.backtests.validation import run_validation
        from test_spot_inputs import T,H
        self.inputs()
        class DeniedRepository(DataRepository):
            def query(self,request,*args,**kwargs):raise PermissionError('repository policy denied')
        with self.assertRaisesRegex(PermissionError,'repository policy denied'):
            run_validation(self.prices,self.spec,self.recipe,self.plan_path,self.root/'denied',
                           dry_run=True,data_repository=DeniedRepository())
        self.assertFalse((self.root/'denied').exists())
        with self.database.transaction(readonly=False) as connection:
            connection.execute('UPDATE public.md_bar SET close=999 WHERE instrument_id=1 AND ts=?',[T+528*H])
        self.database.close()
        with self.assertRaisesRegex(ValueError,'differ from the qualified'):
            self.command(self.root/'mismatch','--dry-run','--duckdb',self.path)
        self.assertFalse((self.root/'mismatch').exists())

    def test_grid_equal_scores_use_existing_hash_tie_break(self):
        self.inputs();self.database.close()
        self.plan['selection']['search']['parameters']={'vol_target':['.000000000001','.000000000002']}
        self.plan_path.write_text(json.dumps(self.plan))
        _,first,preview=self.study('tie','--duckdb',self.path)
        selected=first['selections']['fold-001']
        self.assertEqual([t['score'] for t in selected['trials']],[0,0])
        self.assertEqual(selected['selected_recipe_sha256'],min(c['recipe_sha256'] for c in preview['candidates']))
        self.plan['selection']['search']['parameters']['vol_target'].reverse()
        self.plan_path.write_text(json.dumps(self.plan))
        _,second,_=self.study('tie-reversed','--duckdb',self.path)
        self.assertEqual(second['selections']['fold-001']['selected_candidate'],selected['selected_candidate'])

    def test_grid_runtime_failures_retain_every_trial_and_gate_the_selected_test(self):
        self.inputs();self.database.close()
        raw=json.loads(self.recipe.read_text())
        raw.update(regime_mode='supplied_multiplier',auxiliary_rules=[dict(field='regime',source_id='unavailable',max_age_seconds=3600)])
        self.recipe.write_text(json.dumps(raw));self.plan['recipe_sha256']=hashlib.sha256(self.recipe.read_bytes()).hexdigest()
        self.plan_path.write_text(json.dumps(self.plan))
        output,report,_=self.study('failures','--duckdb',self.path)
        selection=report['selections']['fold-001']
        self.assertEqual(selection['status'],'failed')
        self.assertEqual([t['status'] for t in selection['trials']],['failed']*4)
        for trial in selection['trials']:
            self.assertIn('Missing or stale regime',trial['error'])
            folder=output/'cases/fold-001/training'/trial['artifacts']
            self.assertTrue((folder/'failure.json').is_file())
            self.assertIn('coordinates',decode(json.loads((folder/'case.json').read_text())))
        self.assertFalse((output/'cases/fold-001/run').exists())

    def test_grid_retains_candidate_and_v3_evaluation_bounds(self):
        self.inputs();self.prices=self.root/'missing-prices'
        for name,change,error in [
            ('total',{'max_evaluations':4},'budget exceeded before trials'),
            ('ceiling',{'max_evaluations':8450},'budget must be 1 to 8449')]:
            with self.subTest(name=name):
                self.plan.update(change);self.plan_path.write_text(json.dumps(self.plan))
                with self.assertRaisesRegex(ValueError,error):self.command(self.root/name,'--dry-run')
                self.assertFalse((self.root/name).exists())
        self.inputs();self.plan['selection_rule']='frozen_recipe';self.plan.pop('selection')
        self.plan_path.write_text(json.dumps(self.plan))
        with self.assertRaisesRegex(ValueError,'v3 plans require training_rank'):
            self.command(self.root/'not-ranking','--dry-run')

    def test_documented_grid_example_previews_with_synthetic_inputs(self):
        self.inputs()
        sample=Path(__file__).parents[1]/'examples/momentum.grid-validation.json'
        declared=json.loads(sample.read_text())
        baseline=sample.with_name('momentum.baseline.json')
        self.assertEqual(declared['recipe_sha256'],hashlib.sha256(baseline.read_bytes()).hexdigest())
        # The dates and recipe hash depend on this fixture; all grid/schedule policies use the shipped example.
        declared.update(bounds=self.plan['bounds'],recipe_sha256=self.plan['recipe_sha256'])
        self.plan_path.write_text(json.dumps(declared));self.database.close()
        output=self.root/'documented-preview';self.command(output,'--dry-run','--duckdb',self.path)
        preview=decode(json.loads((output/'resolved-plan.json').read_text()))
        self.assertEqual(preview['evaluation_count'],5)
        self.assertEqual(len(preview['candidates']),4)
        self.assertFalse((output/'cases').exists())
