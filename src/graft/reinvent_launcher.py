"""Standalone LibInvent launcher, copied to SSH runs without installing Graft there.

Some REINVENT LibInvent encoders squeeze away the batch axis for singleton
batches. Restore the documented encoder shapes before the decoder sees them.
The installed REINVENT package and the training/validation data stay untouched.
"""
from functools import wraps


def support_hydrogen_decorations(bond_maker):
    """Interpret a standalone decoration * as H without changing the prior."""
    original = bond_maker.join_scaffolds_and_decorations

    @wraps(original)
    def join(scaffold_smi, decorations_smi, keep_labels_on_atoms=False):
        parts = decorations_smi.split('|')
        if '*' not in parts:
            return original(scaffold_smi, decorations_smi, keep_labels_on_atoms)
        from rdkit import Chem, rdBase
        expanded = '|'.join('*[H]' if part == '*' else part for part in parts)
        with rdBase.BlockLogs():
            mol = original(scaffold_smi, expanded, keep_labels_on_atoms)
            return Chem.RemoveHs(mol) if mol is not None else None

    bond_maker.join_scaffolds_and_decorations = join


def preserve_encoder_dimensions(encoder_class):
    original_forward = encoder_class.forward

    @wraps(original_forward)
    def forward(self, padded_seqs, seq_lengths):
        encoded, (hidden, cell) = original_forward(self, padded_seqs, seq_lengths)
        batch_size = padded_seqs.size(0)
        return (
            encoded.reshape(batch_size, -1, self.num_dimensions),
            (
                hidden.reshape(self.num_layers, batch_size, self.num_dimensions),
                cell.reshape(self.num_layers, batch_size, self.num_dimensions),
            ),
        )

    encoder_class.forward = forward


def main():
    from reinvent.chemistry.library_design import bond_maker
    from reinvent.models.libinvent.models.decorator import Encoder
    from reinvent.Reinvent import main_script

    preserve_encoder_dimensions(Encoder)
    support_hydrogen_decorations(bond_maker)
    return main_script()


if __name__ == "__main__":
    raise SystemExit(main())
