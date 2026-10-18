import React, { useState } from 'react';
import { catalogApi } from '../../api/endpoints/catalog';
import { ApiError, formatErrorMessage } from '../../api/errors';
import { ProductResponse } from '../../api/types';
import { useToast } from '../../app/providers/ToastContext';
import { Alert } from '../../components/ui/Alert';
import { Button } from '../../components/ui/Button';
import { Input } from '../../components/ui/Input';
import { Modal } from '../../components/ui/Modal';

export interface CreateProductModalProps {
  isOpen: boolean;
  onClose: () => void;
  onCreated: (product: ProductResponse) => void;
}

export const CreateProductModal: React.FC<CreateProductModalProps> = ({
  isOpen,
  onClose,
  onCreated,
}) => {
  const { showToast } = useToast();
  const [name, setName] = useState('');
  const [slug, setSlug] = useState('');
  const [description, setDescription] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    if (!name.trim() || !slug.trim()) {
      setError('Product name and slug are required.');
      return;
    }

    setIsSubmitting(true);
    try {
      const created = await catalogApi.createProduct({
        name: name.trim(),
        slug: slug.trim().toLowerCase(),
        description: description.trim() || null,
        attributes: {},
      });

      showToast(`Product "${created.name}" created in DRAFT status.`, 'success');
      onCreated(created);
      handleClose();
    } catch (err: unknown) {
      const msg = err instanceof ApiError ? formatErrorMessage(err) : 'Failed to create product.';
      setError(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleClose = () => {
    setName('');
    setSlug('');
    setDescription('');
    setError(null);
    onClose();
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title="Create New Product"
      description="Add a new catalog product. Products start in DRAFT status until activated."
    >
      {error && <Alert variant="danger" className="mb-4">{error}</Alert>}

      <form onSubmit={handleSubmit}>
        <Input
          label="Product Name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            if (!slug) {
              setSlug(
                e.target.value
                  .toLowerCase()
                  .replace(/[^a-z0-9]+/g, '-')
                  .replace(/^-|-$/g, '')
              );
            }
          }}
          placeholder="Wireless Ergonomic Keyboard"
          required
        />

        <Input
          label="Product Slug"
          value={slug}
          onChange={(e) => setSlug(e.target.value.toLowerCase())}
          placeholder="wireless-ergonomic-keyboard"
          pattern="^[a-z0-9]+(?:-[a-z0-9]+)*$"
          helperText="Lowercase alphanumeric and hyphens (3-80 chars)."
          required
        />

        <div className="form-group">
          <label className="form-label">Description (Optional)</label>
          <textarea
            className="form-textarea"
            rows={3}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Detailed specifications and features..."
            maxLength={4000}
          />
        </div>

        <div className="flex justify-end gap-2 mt-6">
          <Button variant="secondary" onClick={handleClose} disabled={isSubmitting}>
            Cancel
          </Button>
          <Button variant="primary" type="submit" isLoading={isSubmitting}>
            Create Product
          </Button>
        </div>
      </form>
    </Modal>
  );
};
