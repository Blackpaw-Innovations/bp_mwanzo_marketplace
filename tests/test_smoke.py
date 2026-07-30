from odoo.tests.common import TransactionCase


class MwanzoMarketplaceSmokeTest(TransactionCase):
    def test_module_smoke(self):
        self.assertTrue(self.env.registry['ir.module.module'].search([('name', '=', 'mwanzo_marketplace')]))
