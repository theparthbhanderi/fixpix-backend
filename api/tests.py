from unittest import mock

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from rest_framework.test import APIClient, APITestCase
import cv2
import numpy as np

from api.cloudflare_views import _quality_for_plan, _select_edit_model
from subscriptions.models import DailyUsage, SubscriptionPlan, UserSubscription


class CloudflarePlanMappingTests(SimpleTestCase):
	def test_model_mapping_by_plan(self):
		self.assertEqual(_select_edit_model("FREE"), "cheap-fast-model")
		self.assertEqual(_select_edit_model("PRO"), "balanced-model")
		self.assertEqual(_select_edit_model("ELITE"), "best-quality-model")

	def test_quality_mapping_by_plan(self):
		self.assertEqual(_quality_for_plan("FREE"), "low")
		self.assertEqual(_quality_for_plan("PRO"), "medium")
		self.assertEqual(_quality_for_plan("ELITE"), "high")


class PlanAwareEndpointIntegrationTests(APITestCase):
	def setUp(self):
		self.client = APIClient()
		self.user_free = User.objects.create_user(username="api_free", password="xpass123")
		self.user_pro = User.objects.create_user(username="api_pro", password="xpass123")
		self.user_elite = User.objects.create_user(username="api_elite", password="xpass123")

		self.plan_pro = SubscriptionPlan.objects.create(name="pro", display_name="Pro", price_inr=999)
		self.plan_elite = SubscriptionPlan.objects.create(name="elite", display_name="Elite", price_inr=1999)
		UserSubscription.objects.create(user=self.user_pro, plan=self.plan_pro, is_active=True)
		UserSubscription.objects.create(user=self.user_elite, plan=self.plan_elite, is_active=True)

	def _image_file(self, name="img.png"):
		img = np.full((128, 128, 3), 200, dtype=np.uint8)
		ok, buf = cv2.imencode('.png', img)
		self.assertTrue(ok)
		return SimpleUploadedFile(name, buf.tobytes(), content_type="image/png")

	@mock.patch("api.services.stability_service.StabilityService.edit_image")
	@mock.patch("api.services.stability_service.StabilityService.conservative_upscale")
	@mock.patch("api.services.cloudflare_image.edit_image")
	def test_free_pro_elite_edit_outputs_are_differentiated(self, cloudflare_edit_mock, elite_upscale_mock, stability_edit_mock):
		img = np.full((128, 128, 3), 180, dtype=np.uint8)
		ok, buf = cv2.imencode('.png', img)
		self.assertTrue(ok)
		mock_bytes = buf.tobytes()

		cloudflare_edit_mock.return_value = mock_bytes
		stability_edit_mock.return_value = mock_bytes
		elite_upscale_mock.return_value = mock_bytes

		self.client.force_authenticate(user=self.user_free)
		free_res = self.client.post(
			"/api/generate/edit-image/",
			{"image": self._image_file("free.png"), "prompt": "enhance details"},
			format="multipart",
		)
		self.assertEqual(free_res.status_code, 200)
		self.assertEqual(free_res.data["quality"], "low")
		self.assertTrue(free_res.data["watermark"])

		self.client.force_authenticate(user=self.user_pro)
		pro_res = self.client.post(
			"/api/generate/edit-image/",
			{"image": self._image_file("pro.png"), "prompt": "enhance details"},
			format="multipart",
		)
		self.assertEqual(pro_res.status_code, 200)
		self.assertEqual(pro_res.data["quality"], "medium")
		self.assertFalse(pro_res.data["watermark"])

		self.client.force_authenticate(user=self.user_elite)
		elite_res = self.client.post(
			"/api/generate/edit-image/",
			{"image": self._image_file("elite.png"), "prompt": "enhance details"},
			format="multipart",
		)
		self.assertEqual(elite_res.status_code, 200)
		self.assertEqual(elite_res.data["quality"], "high")
		self.assertFalse(elite_res.data["watermark"])

	def test_direct_api_call_is_blocked_after_free_limit(self):
		from django.utils import timezone

		self.client.force_authenticate(user=self.user_free)
		DailyUsage.objects.create(user=self.user_free, date=timezone.localdate(), edits_used=5)

		response = self.client.post(
			"/api/generate/edit-image/",
			{"image": self._image_file("blocked.png"), "prompt": "change sky"},
			format="multipart",
		)
		self.assertIn(response.status_code, [403, 429])
		self.assertFalse(response.data.get("allowed", True))
