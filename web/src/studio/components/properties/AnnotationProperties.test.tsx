import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import AnnotationProperties from './AnnotationProperties';
import type { Annotation } from '../../types';

const baseAnn = (overrides: Partial<Annotation> = {}): Annotation => ({
  id: 'a1',
  type: 'text',
  x: 10,
  y: 20,
  ...overrides,
});

describe('AnnotationProperties', () => {
  it('renders the header and delete button, calls onDelete with id', async () => {
    const onDelete = vi.fn();
    const ann = baseAnn();
    const user = userEvent.setup();

    render(
      <AnnotationProperties
        annotation={ann}
        annotations={[ann]}
        onUpdateAnnotation={vi.fn()}
        onDelete={onDelete}
      />,
    );

    expect(screen.getByText('Annotation Settings')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: '' })); // delete trash button (has icon only)
    expect(onDelete).toHaveBeenCalledWith('a1');
  });

  describe('text annotations', () => {
    it('renders text content + size inputs and emits updates', () => {
      const onUpdate = vi.fn();
      const ann = baseAnn({ type: 'text', text: 'hello', fontSize: 18 });

      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={onUpdate}
          onDelete={vi.fn()}
        />,
      );

      const textarea = screen.getByRole('textbox');
      expect(textarea).toHaveValue('hello');
      fireEvent.change(textarea, { target: { value: 'world' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { text: 'world' });

      const sizeInput = screen.getByRole('spinbutton');
      expect(sizeInput).toHaveValue(18);
      fireEvent.change(sizeInput, { target: { value: '24' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { fontSize: 24 });
    });

    it('uses empty string default for text and 14 default for fontSize', () => {
      const ann = baseAnn({ type: 'text' });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={vi.fn()}
          onDelete={vi.fn()}
        />,
      );

      expect(screen.getByRole('textbox')).toHaveValue('');
      expect(screen.getByRole('spinbutton')).toHaveValue(14);
    });
  });

  describe('rect annotations', () => {
    it('renders width and height with default values', () => {
      const ann = baseAnn({ type: 'rect' });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={vi.fn()}
          onDelete={vi.fn()}
        />,
      );

      const inputs = screen.getAllByRole('spinbutton');
      // width default = 100, height default = 60
      expect(inputs[0]).toHaveValue(100);
      expect(inputs[1]).toHaveValue(60);
      expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    });

    it('clamps width and height to a minimum of 20', () => {
      const onUpdate = vi.fn();
      const ann = baseAnn({ type: 'rect', width: 200, height: 100 });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={onUpdate}
          onDelete={vi.fn()}
        />,
      );

      const inputs = screen.getAllByRole('spinbutton');
      fireEvent.change(inputs[0], { target: { value: '5' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { width: 20 });

      // empty width also falls back to 20 (parseInt → NaN → || 20)
      fireEvent.change(inputs[0], { target: { value: '' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { width: 20 });

      fireEvent.change(inputs[1], { target: { value: '' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { height: 20 });

      fireEvent.change(inputs[0], { target: { value: '300' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { width: 300 });
    });
  });

  describe('circle annotations', () => {
    it('renders only the diameter input, default 80', () => {
      const ann = baseAnn({ type: 'circle' });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={vi.fn()}
          onDelete={vi.fn()}
        />,
      );

      const inputs = screen.getAllByRole('spinbutton');
      expect(inputs).toHaveLength(1);
      expect(inputs[0]).toHaveValue(80);
      expect(screen.getByText(/diameter/i)).toBeInTheDocument();
    });
  });

  describe('arrow annotations', () => {
    it('renders four endpoint inputs with rounded values', () => {
      const ann = baseAnn({ type: 'arrow', x: 10.6, y: 20.4, targetX: 50.7, targetY: 60.2 });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={vi.fn()}
          onDelete={vi.fn()}
        />,
      );

      const inputs = screen.getAllByRole('spinbutton');
      expect(inputs.map((i) => (i as HTMLInputElement).valueAsNumber)).toEqual([11, 20, 51, 60]);
    });

    it('falls back targetX/targetY to start + 100 when undefined', () => {
      const ann = baseAnn({ type: 'arrow', x: 10, y: 20 });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={vi.fn()}
          onDelete={vi.fn()}
        />,
      );

      const inputs = screen.getAllByRole('spinbutton');
      expect((inputs[2] as HTMLInputElement).valueAsNumber).toBe(110);
      expect((inputs[3] as HTMLInputElement).valueAsNumber).toBe(120);
    });

    it('emits parsed float updates for each endpoint, falling back to 0 when invalid', () => {
      const onUpdate = vi.fn();
      const ann = baseAnn({ type: 'arrow', x: 0, y: 0, targetX: 100, targetY: 100 });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={onUpdate}
          onDelete={vi.fn()}
        />,
      );

      const inputs = screen.getAllByRole('spinbutton');
      // Start X — both truthy and falsy paths
      fireEvent.change(inputs[0], { target: { value: '12.5' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { x: 12.5 });
      fireEvent.change(inputs[0], { target: { value: '' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { x: 0 });

      // Start Y falsy
      fireEvent.change(inputs[1], { target: { value: 'abc' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { y: 0 });

      // End X — both paths
      fireEvent.change(inputs[2], { target: { value: '99' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { targetX: 99 });
      fireEvent.change(inputs[2], { target: { value: '' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { targetX: 0 });

      // End Y falsy
      fireEvent.change(inputs[3], { target: { value: '' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { targetY: 0 });
    });
  });

  describe('color picker', () => {
    it('shows the current color and emits updates', () => {
      const onUpdate = vi.fn();
      const ann = baseAnn({ type: 'text', color: '#abcdef' });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={onUpdate}
          onDelete={vi.fn()}
        />,
      );

      const colorInput = document.querySelector('input[type="color"]') as HTMLInputElement;
      expect(colorInput).toHaveValue('#abcdef');
      fireEvent.change(colorInput, { target: { value: '#112233' } });
      expect(onUpdate).toHaveBeenCalledWith('a1', { color: '#112233' });
    });

    it('falls back to the default sage color when none is set', () => {
      const ann = baseAnn({ type: 'text' });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann]}
          onUpdateAnnotation={vi.fn()}
          onDelete={vi.fn()}
        />,
      );
      const colorInput = document.querySelector('input[type="color"]') as HTMLInputElement;
      expect(colorInput).toHaveValue('#65a30d');
    });
  });

  describe('z-order buttons', () => {
    const others: Annotation[] = [
      { id: 'b', type: 'text', x: 0, y: 0, zIndex: 5 },
      { id: 'c', type: 'text', x: 0, y: 0, zIndex: 8 },
      { id: 'd', type: 'text', x: 0, y: 0 }, // zIndex undefined → treated as 0
    ];

    const setup = (annOverrides: Partial<Annotation> = {}) => {
      const onUpdate = vi.fn();
      const ann = baseAnn({ type: 'text', zIndex: 3, ...annOverrides });
      render(
        <AnnotationProperties
          annotation={ann}
          annotations={[ann, ...others]}
          onUpdateAnnotation={onUpdate}
          onDelete={vi.fn()}
        />,
      );
      return { onUpdate, ann };
    };

    it('Bring to Front sets zIndex to max(others) + 1', async () => {
      const { onUpdate } = setup();
      await userEvent.setup().click(screen.getByTitle('Bring to Front'));
      expect(onUpdate).toHaveBeenCalledWith('a1', { zIndex: 9 });
    });

    it('Bring Forward increments by 1, treating missing zIndex as 0', async () => {
      const { onUpdate } = setup({ zIndex: undefined });
      await userEvent.setup().click(screen.getByTitle('Bring Forward'));
      expect(onUpdate).toHaveBeenCalledWith('a1', { zIndex: 1 });
    });

    it('Send Backward decrements by 1', async () => {
      const { onUpdate } = setup({ zIndex: 3 });
      await userEvent.setup().click(screen.getByTitle('Send Backward'));
      expect(onUpdate).toHaveBeenCalledWith('a1', { zIndex: 2 });
    });

    it('Send Backward treats missing zIndex as 0 (→ -1)', async () => {
      const { onUpdate } = setup({ zIndex: undefined });
      await userEvent.setup().click(screen.getByTitle('Send Backward'));
      expect(onUpdate).toHaveBeenCalledWith('a1', { zIndex: -1 });
    });

    it('Send to Back sets zIndex to min(others) - 1', async () => {
      const { onUpdate } = setup();
      await userEvent.setup().click(screen.getByTitle('Send to Back'));
      // Math.min(3, 5, 8, 0, 0) = 0; minus 1 = -1
      expect(onUpdate).toHaveBeenCalledWith('a1', { zIndex: -1 });
    });
  });
});
