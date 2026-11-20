import { render, screen, within } from '@testing-library/react';
import { expect, it } from 'vitest';
import { DeclarationRunBudget } from './DeclarationRunBudget';

it('shows tokens and runtime without presenting unpriced usage as zero dollars', () => {
  render(<DeclarationRunBudget runs={[{stages:[],usage_status:'REPORTED',reported_input_tokens:14365,reported_output_tokens:11747,id:'run',model:'synthetic-local',input_tokens:14365,output_tokens:11747,runtime_seconds:329.6,estimated_cost_usd:null}]} />);
  expect(screen.getByText('kein Preis bekannt')).toBeVisible();
  expect(screen.getByText('14.365')).toBeVisible();
  expect(screen.getByText('11.747')).toBeVisible();
  expect(screen.getByText('330 s')).toBeVisible();
  expect(screen.queryByText(/0[,.]00.*USD/)).not.toBeInTheDocument();
});


it('keeps a known historical estimate in USD', () => {
  render(<DeclarationRunBudget runs={[{stages:[],usage_status:'REPORTED',reported_input_tokens:100,reported_output_tokens:20,id:'priced',model:'known-model',input_tokens:100,output_tokens:20,runtime_seconds:2,estimated_cost_usd:'0.012345'}]} />);
  expect(screen.getByText('0,012345 USD')).toBeVisible();
  expect(screen.queryByText('kein Preis bekannt')).not.toBeInTheDocument();
});


it('labels active or failed reservation counters without claiming measured consumption', () => {
  render(<DeclarationRunBudget runs={[{stages:[],id:'reserved',model:'synthetic-local',input_tokens:10000,output_tokens:12000,reported_input_tokens:null,reported_output_tokens:null,usage_status:'RESERVED',runtime_seconds:90,estimated_cost_usd:null}]} />);
  expect(screen.getByText('Reservierte Eingabetoken')).toBeVisible();
  expect(screen.getByText('Reservierte Ausgabetoken')).toBeVisible();
  expect(screen.getByText(/Der tatsächliche Tokenverbrauch ist noch nicht bekannt/)).toBeVisible();
  expect(screen.queryByText('Eingabetoken',{exact:true})).not.toBeInTheDocument();
});

it('keeps partial provider usage separate from conservative accounted totals', () => {
  render(<DeclarationRunBudget runs={[{stages:[],id:'partial',model:'synthetic-local',input_tokens:10000,output_tokens:12000,reported_input_tokens:30,reported_output_tokens:20,usage_status:'PARTIAL',runtime_seconds:90,estimated_cost_usd:null}]} />);
  expect(screen.getByText('Gemeldete Eingabetoken')).toBeVisible();
  expect(screen.getByText('30')).toBeVisible();
  expect(screen.getByText('Konservativ erfasste Eingabetoken')).toBeVisible();
  expect(screen.getByText('10.000')).toBeVisible();
});

it('does not present unknown usage as measured zero', () => {
  render(<DeclarationRunBudget runs={[{stages:[],id:'unknown',model:'synthetic-local',input_tokens:0,output_tokens:0,reported_input_tokens:null,reported_output_tokens:null,usage_status:'UNKNOWN',runtime_seconds:0,estimated_cost_usd:null}]} />);
  expect(screen.getByText('Tokenverbrauch unbekannt')).toBeVisible();
  expect(screen.queryByText('0',{exact:true})).not.toBeInTheDocument();
});


const reading = {stage:'READING',model:'gpt-5.4-mini',usage_status:'REPORTED',reported_input_tokens:1800,reported_output_tokens:300,input_tokens:1800,output_tokens:300,estimated_cost_usd:'0.002700'} as const;
const extraction = {stage:'EXTRACTION',model:'gpt-5.6-luna',usage_status:'REPORTED',reported_input_tokens:7000,reported_output_tokens:1100,input_tokens:7000,output_tokens:1100,estimated_cost_usd:'0.002720'} as const;
const total = {id:'run',model:'gpt-5.6-luna',usage_status:'REPORTED',reported_input_tokens:8800,reported_output_tokens:1400,input_tokens:8800,output_tokens:1400,runtime_seconds:42,estimated_cost_usd:'0.005420'} as const;

it('shows reading and extraction apart, each with its model, tokens and cost, and then the total', () => {
  render(<DeclarationRunBudget runs={[{...total,stages:[reading,extraction]}]} />);
  const read = screen.getByRole('group', {name:'Dokumente einlesen'});
  expect(within(read).getByText('gpt-5.4-mini')).toBeVisible();
  expect(within(read).getByText('1.800')).toBeVisible();
  expect(within(read).getByText('300')).toBeVisible();
  expect(within(read).getByText('0,0027 USD')).toBeVisible();
  const extract = screen.getByRole('group', {name:'Informationen extrahieren'});
  expect(within(extract).getByText('gpt-5.6-luna')).toBeVisible();
  expect(within(extract).getByText('7.000')).toBeVisible();
  expect(within(extract).getByText('0,00272 USD')).toBeVisible();
  const sum = screen.getByRole('group', {name:'Gesamt'});
  expect(within(sum).getByText('8.800')).toBeVisible();
  expect(within(sum).getByText('42 s')).toBeVisible();
  expect(within(sum).getByText('0,00542 USD')).toBeVisible();
  const order = screen.getAllByRole('group').map(group => group.getAttribute('aria-labelledby') && group.querySelector('h4')?.textContent);
  expect(order).toEqual(['Dokumente einlesen','Informationen extrahieren','Gesamt']);
});

it('shows no empty reading block for a run that read no page', () => {
  render(<DeclarationRunBudget runs={[{...total,stages:[extraction]}]} />);
  expect(screen.queryByRole('group', {name:'Dokumente einlesen'})).not.toBeInTheDocument();
  expect(screen.getByRole('group', {name:'Informationen extrahieren'})).toBeVisible();
});

it('keeps what a failed reading consumed and shows no extraction block for it', () => {
  const failed = {...reading,reported_input_tokens:40,reported_output_tokens:3,input_tokens:40,output_tokens:3,estimated_cost_usd:'0.000002'};
  render(<DeclarationRunBudget runs={[{...total,input_tokens:40,output_tokens:3,reported_input_tokens:40,reported_output_tokens:3,stages:[failed]}]} />);
  expect(within(screen.getByRole('group', {name:'Dokumente einlesen'})).getByText('40')).toBeVisible();
  expect(screen.queryByRole('group', {name:'Informationen extrahieren'})).not.toBeInTheDocument();
});

it('uses the usage vocabulary and the price wording of each stage on its own', () => {
  const reserved = {...extraction,usage_status:'RESERVED',reported_input_tokens:null,reported_output_tokens:null,input_tokens:500,output_tokens:12000,estimated_cost_usd:null} as const;
  const unpriced = {...reading,estimated_cost_usd:null} as const;
  render(<DeclarationRunBudget runs={[{...total,usage_status:'PARTIAL',estimated_cost_usd:null,stages:[unpriced,reserved]}]} />);
  const read = screen.getByRole('group', {name:'Dokumente einlesen'});
  expect(within(read).getByText('Eingabetoken')).toBeVisible();
  expect(within(read).getByText('kein Preis bekannt')).toBeVisible();
  const extract = screen.getByRole('group', {name:'Informationen extrahieren'});
  expect(within(extract).getByText('Reservierte Eingabetoken')).toBeVisible();
  expect(within(extract).getByText('12.000')).toBeVisible();
  expect(within(extract).getByText('kein Preis bekannt')).toBeVisible();
  expect(within(extract).getByText(/Der tatsächliche Tokenverbrauch ist noch nicht bekannt/)).toBeVisible();
  expect(within(extract).queryByText('Eingabetoken',{exact:true})).not.toBeInTheDocument();
});

it('does not present the unknown usage of a stage as measured zero', () => {
  const unknown = {...reading,usage_status:'UNKNOWN',reported_input_tokens:null,reported_output_tokens:null,input_tokens:0,output_tokens:0,estimated_cost_usd:null} as const;
  render(<DeclarationRunBudget runs={[{...total,stages:[unknown]}]} />);
  expect(within(screen.getByRole('group', {name:'Dokumente einlesen'})).getByText('Tokenverbrauch unbekannt')).toBeVisible();
});
