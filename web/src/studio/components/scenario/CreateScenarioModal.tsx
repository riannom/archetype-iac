import React, { useState, useCallback } from 'react';
import { Modal, ModalFooter } from '../../../components/ui/Modal';
import { Button } from '../../../components/ui/Button';
import { Input } from '../../../components/ui/Input';

interface CreateScenarioModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreate: (filename: string) => void;
}

const CreateScenarioModal: React.FC<CreateScenarioModalProps> = ({ isOpen, onClose, onCreate }) => {
  const [name, setName] = useState('');
  const [error, setError] = useState('');

  const handleSubmit = useCallback((e?: React.FormEvent) => {
    e?.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError('Filename is required');
      return;
    }
    if (!/^[\w][\w\-.]*$/.test(trimmed)) {
      setError('Only letters, numbers, hyphens, underscores, and dots allowed');
      return;
    }
    const filename = trimmed.endsWith('.yml') || trimmed.endsWith('.yaml') ? trimmed : `${trimmed}.yml`;
    onCreate(filename);
    setName('');
    setError('');
    onClose();
  }, [name, onCreate, onClose]);

  const handleClose = useCallback(() => {
    setName('');
    setError('');
    onClose();
  }, [onClose]);

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title="New Scenario" size="sm">
      <form onSubmit={handleSubmit}>
        <Input
          label="Filename"
          value={name}
          onChange={e => { setName(e.target.value); setError(''); }}
          placeholder="failover_test.yml"
          error={error}
          size="sm"
          autoFocus
        />
        <ModalFooter>
          <Button variant="ghost" onClick={handleClose} type="button">Cancel</Button>
          <Button variant="primary" type="submit">Create</Button>
        </ModalFooter>
      </form>
    </Modal>
  );
};

export default CreateScenarioModal;
