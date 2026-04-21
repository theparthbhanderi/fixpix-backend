PLAN_LIMITS = {
    "free": {
        "photo_restoration": 3,
        "colorization": 3,
        "background_remove": 3,
        "upscaling": 2,
        "fact_checker": 5,
        "live_news": -1,  # unlimited
        "image_fact_checker": 0,  # not available
    },
    "pro": {
        "photo_restoration": 50,
        "colorization": 50,
        "background_remove": 50,
        "upscaling": 30,
        "fact_checker": 50,
        "live_news": -1,
        "image_fact_checker": 20,
    },
    "elite": {
        "photo_restoration": -1,
        "colorization": -1,
        "background_remove": -1,
        "upscaling": -1,
        "fact_checker": -1,
        "live_news": -1,
        "image_fact_checker": -1,
    },
    "pro_yearly": {
        "photo_restoration": 50,
        "colorization": 50,
        "background_remove": 50,
        "upscaling": 30,
        "fact_checker": 50,
        "live_news": -1,
        "image_fact_checker": 20,
    },
    "elite_yearly": {
        "photo_restoration": -1,
        "colorization": -1,
        "background_remove": -1,
        "upscaling": -1,
        "fact_checker": -1,
        "live_news": -1,
        "image_fact_checker": -1,
    }
}

# Strict backend plan configuration for image workflows.
# NOTE: We keep PLAN_LIMITS above for legacy feature checks used by other modules.
PLAN_CONFIG = {
    "FREE": {
        "maxTasksPerDay": 5,
        "watermark": True,
        "backgroundRemovalLimit": 3,
        "upscalingLimit": 1,
        "batchProcessing": False,
        "priorityProcessing": False,
        "modelAccess": "basic",
    },
    "PRO": {
        "maxTasksPerDay": 50,
        "watermark": False,
        "backgroundRemovalLimit": 50,
        "upscalingLimit": 30,
        "batchProcessing": True,
        "priorityProcessing": True,
        "modelAccess": "advanced",
    },
    "ELITE": {
        "maxTasksPerDay": "unlimited",
        "watermark": False,
        "backgroundRemovalLimit": "unlimited",
        "upscalingLimit": "unlimited",
        "batchProcessing": True,
        "priorityProcessing": True,
        "modelAccess": "advanced",
    },
}

# Maps subscription plan records to effective enforcement tier.
PLAN_NAME_TO_TIER = {
    "free": "FREE",
    "pro": "PRO",
    "pro_yearly": "PRO",
    "elite": "ELITE",
    "elite_yearly": "ELITE",
}
