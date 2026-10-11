# Split from 0002 so the column drops run in their own transaction, after
# 0002's data copy has committed.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('purchase', '0002_brd_pr_po_return_debitnote'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='debitnote',
            name='purchase_return',
        ),
        migrations.RemoveField(
            model_name='purchaseorder',
            name='purchase_requisition',
        ),
    ]
