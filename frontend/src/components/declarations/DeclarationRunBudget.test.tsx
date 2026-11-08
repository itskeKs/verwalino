import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { DeclarationRunBudget } from './DeclarationRunBudget';

it('shows tokens and runtime without presenting unpriced usage as zero dollars', () => {
  render(<DeclarationRunBudget runs={[{id:'run',model:'synthetic-local',input_tokens:14365,output_tokens:11747,runtime_seconds:329.6,estimated_cost_usd:null}]} />);
  expect(screen.getByText('kein Preis bekannt')).toBeVisible();
  expect(screen.getByText('14.365')).toBeVisible();
  expect(screen.getByText('11.747')).toBeVisible();
  expect(screen.getByText('330 s')).toBeVisible();
  expect(screen.queryByText(/0[,.]00.*USD/)).not.toBeInTheDocument();
});


it('keeps a known historical estimate in USD', () => {
  render(<DeclarationRunBudget runs={[{id:'priced',model:'known-model',input_tokens:100,output_tokens:20,runtime_seconds:2,estimated_cost_usd:'0.012345'}]} />);
  expect(screen.getByText('0,012345 USD')).toBeVisible();
  expect(screen.queryByText('kein Preis bekannt')).not.toBeInTheDocument();
});
