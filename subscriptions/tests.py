from unittest import mock

from django.contrib.auth.models import User
from django.core.cache import cache
from django.db import OperationalError
from django.test import RequestFactory, TestCase
from django.utils import timezone
from api.models import ImageProject
from rest_framework.test import APIClient

from subscriptions.models import DailyUsage, SubscriptionPlan, UserPlan, UserSubscription
from subscriptions.plan_enforcement import check_user_plan_for_request, get_today_usage, record_successful_usage, resolve_user_plan


class PlanEnforcementTests(TestCase):
	def setUp(self):
		self.factory = RequestFactory()
		self.client = APIClient()
		cache.clear()
		self.user_free = User.objects.create_user(username="free_user", password="test-pass-123")
		self.user_pro = User.objects.create_user(username="pro_user", password="test-pass-123")
		self.user_elite = User.objects.create_user(username="elite_user", password="test-pass-123")

		self.plan_pro = SubscriptionPlan.objects.create(name="pro", display_name="Pro", price_inr=999)
		self.plan_elite = SubscriptionPlan.objects.create(name="elite", display_name="Elite", price_inr=1999)
		self.plan_pro_yearly = SubscriptionPlan.objects.create(name="pro_yearly", display_name="Pro Yearly", price_inr=9999)
		self.client.force_authenticate(user=self.user_pro)

	def _request_for(self, user):
		request = self.factory.post("/api/test")
		request.user = user
		return request

	def test_resolve_user_plan_uses_subscription_source_of_truth(self):
		UserSubscription.objects.create(user=self.user_pro, plan=self.plan_pro_yearly, is_active=True)
		self.assertEqual(resolve_user_plan(self.user_pro), "PRO")

	def test_free_limit_is_enforced_in_backend(self):
		UserPlan.objects.create(user=self.user_free, plan="FREE", daily_usage=5)
		DailyUsage.objects.create(user=self.user_free, date=timezone.localdate(), edits_used=5)
		request = self._request_for(self.user_free)

		result = check_user_plan_for_request(request, consume=False)

		self.assertFalse(result["allowed"])
		self.assertEqual(result["status_code"], 403)
		self.assertIn("Upgrade", result["error"])
		self.assertIn("upgradeMessage", result)
		self.assertEqual(result["plan"], "FREE")

	def test_elite_not_capped_by_free_limit(self):
		UserSubscription.objects.create(user=self.user_elite, plan=self.plan_elite, is_active=True)
		UserPlan.objects.create(user=self.user_elite, plan="ELITE", daily_usage=999)
		request = self._request_for(self.user_elite)

		result = check_user_plan_for_request(request, consume=False)

		self.assertTrue(result["allowed"])
		self.assertEqual(result["plan"], "ELITE")

	def test_request_always_gets_plan_and_config(self):
		request = self._request_for(self.user_free)

		result = check_user_plan_for_request(request, consume=False)

		self.assertTrue(result["allowed"])
		self.assertEqual(request.plan, "FREE")
		self.assertIn("modelAccess", request.plan_config)

	def test_missing_userplan_sync_table_path_falls_back_to_subscription(self):
		UserSubscription.objects.create(user=self.user_pro, plan=self.plan_pro, is_active=True)
		request = self._request_for(self.user_pro)

		with mock.patch(
			"subscriptions.plan_enforcement.UserPlan.objects.get_or_create",
			side_effect=OperationalError("table missing"),
		):
			result = check_user_plan_for_request(request, consume=False)

		self.assertTrue(result["allowed"])
		self.assertEqual(result["plan"], "PRO")
		self.assertEqual(result["plan_config"]["modelAccess"], "advanced")

	def test_subscription_upgrade_reflects_immediately(self):
		subscription = UserSubscription.objects.create(user=self.user_pro, plan=self.plan_pro, is_active=True)
		self.assertEqual(resolve_user_plan(self.user_pro), "PRO")

		subscription.plan = self.plan_elite
		subscription.save(update_fields=["plan"])

		request = self._request_for(self.user_pro)
		result = check_user_plan_for_request(request, consume=False)

		self.assertTrue(result["allowed"])
		self.assertEqual(result["plan"], "ELITE")

	def test_get_today_usage_creates_daily_row(self):
		usage = get_today_usage(self.user_free)
		self.assertEqual(usage.edits_used, 0)
		self.assertEqual(DailyUsage.objects.filter(user=self.user_free).count(), 1)

	def test_record_successful_usage_increments_only_after_success_commit(self):
		request = self._request_for(self.user_free)
		request.plan = "FREE"
		request._usage_feature_key = "background_remove"

		check_result = check_user_plan_for_request(request, feature_key="background_remove", consume=False)
		self.assertTrue(check_result["allowed"])

		usage_before = get_today_usage(self.user_free)
		self.assertEqual(usage_before.edits_used, 0)

		record_successful_usage(request, feature_key="background_remove")
		usage_after = get_today_usage(self.user_free)
		self.assertEqual(usage_after.edits_used, 1)
		self.assertEqual(usage_after.bg_remove_used, 1)

	def test_rate_limit_blocks_after_too_many_requests(self):
		request = self._request_for(self.user_free)
		for _ in range(10):
			result = check_user_plan_for_request(request, feature_key="edit", consume=False)
			self.assertTrue(result["allowed"])

		blocked = check_user_plan_for_request(request, feature_key="edit", consume=False)
		self.assertFalse(blocked["allowed"])
		self.assertEqual(blocked["reason"], "too_many_requests")

	@mock.patch.dict("subscriptions.plan_enforcement.RATE_LIMITS_PER_MINUTE", {"FREE": 100, "PRO": 30, "ELITE": 120})
	def test_free_ip_throttle_blocks_multi_account_style_spam(self):
		request = self._request_for(self.user_free)
		request.META["REMOTE_ADDR"] = "1.2.3.4"
		for _ in range(20):
			result = check_user_plan_for_request(request, feature_key="edit", consume=False)
			self.assertTrue(result["allowed"])

		blocked = check_user_plan_for_request(request, feature_key="edit", consume=False)
		self.assertFalse(blocked["allowed"])
		self.assertEqual(blocked["reason"], "ip_throttled")

	def test_concurrency_limit_for_free_user(self):
		ImageProject.objects.create(user=self.user_free, status="pending")
		ImageProject.objects.create(user=self.user_free, status="processing")

		request = self._request_for(self.user_free)
		result = check_user_plan_for_request(request, feature_key="edit", consume=False)
		self.assertFalse(result["allowed"])
		self.assertEqual(result["reason"], "concurrency_limit_reached")

	def test_cost_cap_blocks_expensive_free_request(self):
		request = self._request_for(self.user_free)
		result = check_user_plan_for_request(request, feature_key="upscaling", consume=False, estimated_cost="0.02")
		self.assertFalse(result["allowed"])
		self.assertEqual(result["reason"], "cost_limit_exceeded")

	@mock.patch("subscriptions.views.get_razorpay_client")
	@mock.patch("api.services.supabase_service.supabase_service.admin_update_user_metadata", return_value=True)
	def test_payment_success_activates_subscription_instantly(self, _sync_mock, get_client_mock):
		mock_client = mock.Mock()
		mock_client.utility.verify_payment_signature.return_value = None
		get_client_mock.return_value = mock_client

		response = self.client.post(
			"/api/subscriptions/verify-payment/",
			{
				"razorpay_order_id": "order_123",
				"razorpay_payment_id": "pay_123",
				"razorpay_signature": "sig_123",
				"plan_id": "pro",
			},
			format="json",
		)

		self.assertEqual(response.status_code, 200)
		sub = UserSubscription.objects.get(user=self.user_pro)
		self.assertTrue(sub.is_active)
		self.assertEqual(sub.plan.name, "pro")

		status_res = self.client.get("/api/subscriptions/status/")
		self.assertEqual(status_res.status_code, 200)
		self.assertEqual(status_res.data["plan"]["tier"], "PRO")

	@mock.patch("subscriptions.views.get_razorpay_client")
	def test_payment_invalid_signature_does_not_upgrade(self, get_client_mock):
		mock_client = mock.Mock()
		mock_client.utility.verify_payment_signature.side_effect = Exception("bad signature")
		get_client_mock.return_value = mock_client

		response = self.client.post(
			"/api/subscriptions/verify-payment/",
			{
				"razorpay_order_id": "order_123",
				"razorpay_payment_id": "pay_123",
				"razorpay_signature": "sig_bad",
				"plan_id": "pro",
			},
			format="json",
		)

		self.assertEqual(response.status_code, 500)
		self.assertFalse(UserSubscription.objects.filter(user=self.user_pro, is_active=True).exists())
