/* ============================================================================
   Read-only SQL Server login for the grid data agent.
   Run once in SSMS as an administrator. Choose your own strong password, then put it in .env:
       AGENT_MSSQL_USER=grid_agent_reader
       AGENT_MSSQL_PASSWORD=<the password below>

   The login can only SELECT from schema [datamart] in database [MonitoringLab]:
   no staging/etl/dbo tables, no writes, no DDL, no server-level permissions.
   ============================================================================ */

USE [master];
GO
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = N'grid_agent_reader')
    CREATE LOGIN [grid_agent_reader]
        WITH PASSWORD = N'Change-Me-To-A-Strong-Passw0rd!',
             DEFAULT_DATABASE = [MonitoringLab],
             CHECK_POLICY = ON;
GO

USE [MonitoringLab];
GO
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = N'grid_agent_reader')
    CREATE USER [grid_agent_reader] FOR LOGIN [grid_agent_reader] WITH DEFAULT_SCHEMA = [datamart];
GO

-- Read access to the datamart schema only (not db_datareader, which would cover every schema).
GRANT SELECT ON SCHEMA::[datamart] TO [grid_agent_reader];
GO

-- Belt and braces: explicitly deny changes and the other schemas.
DENY INSERT, UPDATE, DELETE, ALTER, EXECUTE ON SCHEMA::[datamart] TO [grid_agent_reader];
DENY SELECT ON SCHEMA::[staging] TO [grid_agent_reader];
DENY SELECT ON SCHEMA::[etl]     TO [grid_agent_reader];
GO

-- Check: should list SELECT (GRANT) and the DENY rows.
SELECT pr.name AS principal, pe.permission_name, pe.state_desc, s.name AS [schema]
FROM sys.database_permissions pe
JOIN sys.database_principals pr ON pr.principal_id = pe.grantee_principal_id
LEFT JOIN sys.schemas s ON s.schema_id = pe.major_id AND pe.class_desc = 'SCHEMA'
WHERE pr.name = N'grid_agent_reader';
