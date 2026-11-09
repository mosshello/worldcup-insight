<script setup>
import { ref, watch, onBeforeUnmount } from 'vue';
import { api } from './api';
import ObservationEvidence from './ObservationEvidence.vue';
const props=defineProps({fixtureId:String,competition:String});
const summary=ref(null),error=ref('');let sequence=0;
async function load(){const current=++sequence;summary.value=null;error.value='';if(!['eng.premier','esp.laliga','ger.bundesliga','ita.serie_a','fra.ligue1'].includes(props.competition))return;try{const result=await api.observationDetail(props.fixtureId);if(current===sequence)summary.value=result.evidence_summary||null;}catch(e){if(current===sequence)error.value=e.message==='比赛不存在'?'观察资料尚未关联':'赛前资料读取失败';}}
watch(()=>[props.fixtureId,props.competition],load,{immediate:true});onBeforeUnmount(()=>sequence++);
</script>
<template><t-alert v-if="error" theme="warning" class="mb16">{{error}}<t-button variant="text" @click="load">重试</t-button></t-alert><ObservationEvidence :summary="summary" /></template>
