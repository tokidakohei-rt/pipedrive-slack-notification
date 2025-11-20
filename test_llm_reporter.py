import unittest
from unittest.mock import patch, MagicMock
import llm_reporter

class TestLLMReporter(unittest.TestCase):

    @patch('llm_reporter.os.getenv')
    @patch('llm_reporter.genai.GenerativeModel')
    @patch('llm_reporter.genai.configure')
    def test_generate_report_success(self, mock_configure, mock_model_cls, mock_getenv):
        # Setup mocks
        mock_getenv.return_value = 'fake_api_key'
        
        mock_model_instance = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "AI Report Content"
        mock_model_instance.generate_content.return_value = mock_response
        mock_model_cls.return_value = mock_model_instance

        # Input data
        stage_companies = {
            'Stage A': {'Company 1', 'Company 2'},
            'Stage B': set()
        }

        # Execute
        report = llm_reporter.generate_report(stage_companies)

        # Verify
        self.assertEqual(report, "AI Report Content")
        mock_configure.assert_called_with(api_key='fake_api_key')
        mock_model_instance.generate_content.assert_called_once()
        
        # Check if prompt contains company names
        args, _ = mock_model_instance.generate_content.call_args
        prompt = args[0]
        self.assertIn('Stage A', prompt)
        self.assertIn('Company 1', prompt)
        self.assertIn('Company 2', prompt)

    @patch('llm_reporter.os.getenv')
    def test_generate_report_no_api_key(self, mock_getenv):
        mock_getenv.return_value = None
        
        stage_companies = {'Stage A': {'Company 1'}}
        report = llm_reporter.generate_report(stage_companies)
        
        self.assertIn("GOOGLE_API_KEYが設定されていません", report)

    @patch('llm_reporter.os.getenv')
    def test_generate_report_no_deals(self, mock_getenv):
        mock_getenv.return_value = 'fake_api_key'
        
        stage_companies = {'Stage A': set(), 'Stage B': set()}
        report = llm_reporter.generate_report(stage_companies)
        
        self.assertIn("案件がありません", report)

if __name__ == '__main__':
    unittest.main()
