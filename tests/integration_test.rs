use anyhow::Result;
use sigmaos_component::{Config, Service};

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_desktop_config_aaa() -> Result<()> {
        // Arrange
        let config = Config::default();

        // Act
        let result = config.process();

        // Assert
        assert!(result.is_ok());
        Ok(())
    }

    #[test]
    fn test_desktop_config_custom_log_level_aaa() -> Result<()> {
        // Arrange
        let custom_config = Config::new("trace");

        // Act
        let result = custom_config.process();

        // Assert
        assert!(result.is_ok());
        Ok(())
    }

    #[test]
    fn test_desktop_config_service_lifecycle_aaa() -> Result<()> {
        // Arrange
        let config = Config::default();
        let mut service = Service::new(config);
        assert!(!service.is_running());

        // Act & Assert - Start
        service.start()?;
        assert!(service.is_running());

        // Act & Assert - Redundant start fails cleanly
        assert!(service.start().is_err());

        // Act & Assert - Stop
        service.stop()?;
        assert!(!service.is_running());

        // Act & Assert - Redundant stop fails cleanly
        assert!(service.stop().is_err());

        Ok(())
    }
}
