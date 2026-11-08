import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { DeclarationRunBudget } from './DeclarationRunBudget';

it('shows tokens and runtime without presenting unpriced usage as zero dollars', () => {
  render(<DeclarationRunBudget runs={[{usage_status:'REPORTED',reported_input_tokens:14365,reported_output_tokens:11747,id:'run',model:'synthetic-local',input_tokens:14365,output_tokens:11747,runtime_seconds:329.6,estimated_cost_usd:null}]} />);
  expect(screen.getByText('kein Preis bekannt')).toBeVisible();
  expect(screen.getByText('14.365')).toBeVisible();
  expect(screen.getByText('11.747')).toBeVisible();
  expect(screen.getByText('330 s')).toBeVisible();
  expect(screen.queryByText(/0[,.]00.*USD/)).not.toBeInTheDocument();
});


it('keeps a known historical estimate in USD', () => {
  render(<DeclarationRunBudget runs={[{usage_status:'REPORTED',reported_input_tokens:100,reported_output_tokens:20,id:'priced',model:'known-model',input_tokens:100,output_tokens:20,runtime_seconds:2,estimated_cost_usd:'0.012345'}]} />);
  expect(screen.getByText('0,012345 USD')).toBeVisible();
  expect(screen.queryByText('kein Preis bekannt')).not.toBeInTheDocument();
});


it('labels active or failed reservation counters without claiming measured consumption', () => {
  render(<DeclarationRunBudget runs={[{id:'reserved',model:'synthetic-local',input_tokens:10000,output_tokens:12000,reported_input_tokens:null,reported_output_tokens:null,usage_status:'RESERVED',runtime_seconds:90,estimated_cost_usd:null}]} />);
  expect(screen.getByText('Reservierte Eingabetoken')).toBeVisible();
  expect(screen.getByText('Reservierte Ausgabetoken')).toBeVisible();
  expect(screen.getByText(/Der tatsächliche Tokenverbrauch ist noch nicht bekannt/)).toBeVisible();
  expect(screen.queryByText('Eingabetoken',{exact:true})).not.toBeInTheDocument();
});

it('keeps partial provider usage separate from conservative accounted totals', () => {
  render(<DeclarationRunBudget runs={[{id:'partial',model:'synthetic-local',input_tokens:10000,output_tokens:12000,reported_input_tokens:30,reported_output_tokens:20,usage_status:'PARTIAL',runtime_seconds:90,estimated_cost_usd:null}]} />);
  expect(screen.getByText('Gemeldete Eingabetoken')).toBeVisible();
  expect(screen.getByText('30')).toBeVisible();
  expect(screen.getByText('Konservativ erfasste Eingabetoken')).toBeVisible();
  expect(screen.getByText('10.000')).toBeVisible();
});

it('does not present unknown usage as measured zero', () => {
  render(<DeclarationRunBudget runs={[{id:'unknown',model:'synthetic-local',input_tokens:0,output_tokens:0,reported_input_tokens:null,reported_output_tokens:null,usage_status:'UNKNOWN',runtime_seconds:0,estimated_cost_usd:null}]} />);
  expect(screen.getByText('Tokenverbrauch unbekannt')).toBeVisible();
  expect(screen.queryByText('0',{exact:true})).not.toBeInTheDocument();
});
